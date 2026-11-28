"""운동 세션의 생명주기. 웹에서 시작/종료를 누르면 분석기가 세션을 열고 닫는다.

    idle(준비)  --start-->  active(운동 중, 반복을 백엔드에 올림)  --stop-->  idle(준비)

idle에서도 영상 분석과 자세 표시는 돌아간다(자세를 잡는 동안 관절선 색을 보라고). 다만 횟수는 세지 않고 업로드도 안 한다.
active가 시작될 때마다 분석기를 새로 만들어 0회부터 센다. 운동을 바꾸면 이전 세션은 닫고 새 부품으로 바꾼다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from aipt_analyzer.analyzer import Analyzer
from aipt_analyzer.config import ExerciseConfig, load_exercise_config
from aipt_analyzer.control import Command
from aipt_analyzer.overlay import Overlay
from aipt_analyzer.pipeline import PipelineParts
from aipt_analyzer.recorder import SessionRecorder
from aipt_analyzer.uploader import BackendClient, UploadWorker

log = logging.getLogger("aipt.station")


class Station:
    def __init__(
        self,
        cfg: ExerciseConfig,
        *,
        client: BackendClient | None = None,
        build_overlay: Callable[[ExerciseConfig], Overlay | None] = lambda cfg: None,
        record_dir: str | None = None,
        info=None,  # AnalysisPublisher (set_config / set_session)
        autostart: bool = True,
        load_config: Callable[[str], ExerciseConfig] = load_exercise_config,
    ) -> None:
        self._client = client
        self._build_overlay = build_overlay
        self._record_dir = record_dir
        self._info = info
        self._load = load_config
        self.cfg = cfg
        self.state = "idle"
        self.session_id: int | None = None
        self.started_at: datetime | None = None
        self.last: dict | None = None  # 방금 끝난 세션 요약 (웹이 리포트로 이동하는 데 쓴다)
        self.parts = self._idle_parts(cfg)
        if autostart:
            self._open(cfg)
            self.parts = self._active_parts(cfg)
        self._publish()

    # ---- 부품 ------------------------------------------------------------
    def _idle_parts(self, cfg: ExerciseConfig) -> PipelineParts:
        return PipelineParts(Analyzer(cfg), self._build_overlay(cfg), None, None)

    def _active_parts(self, cfg: ExerciseConfig) -> PipelineParts:
        recorder = SessionRecorder(self._record_dir, cfg) if self._record_dir else None
        worker = UploadWorker(self._client).start() if self._client is not None and self.session_id else None
        return PipelineParts(Analyzer(cfg), self._build_overlay(cfg), recorder, worker)

    def _open(self, cfg: ExerciseConfig) -> None:
        self.session_id = None
        if self._client is not None:
            try:
                self.session_id = self._client.start_session(cfg.exercise, cfg)
            except Exception as e:  # 백엔드가 안 돼도 화면 분석은 계속한다
                log.error("세션 시작 실패, 업로드 없이 계속합니다: %s", e)
        self.state = "active"
        self.started_at = datetime.now(timezone.utc)
        self.last = None
        log.info("세션 시작: %s (id=%s)", cfg.exercise, self.session_id)

    def _close(self) -> None:
        """진행 중인 세션을 닫는다 (남은 반복 전송, 세션 종료, 기록 파일 닫기)."""
        old = self.parts
        finished = None
        if old.worker:
            old.worker.close()
            finished = self._client.last_finished if self._client is not None else None
        if old.recorder:
            old.recorder.close()
        reps = old.analyzer.counter.reps
        self.last = {
            "id": (finished or {}).get("id", self.session_id),
            "exercise": self.cfg.exercise,
            "total_reps": (finished or {}).get("total_reps", reps),
            "avg_score": (finished or {}).get("avg_score"),
            "saved": finished is not None,
        }
        log.info("세션 종료: %s", self.last)
        self.state = "idle"
        self.session_id = None
        self.started_at = None

    def _publish(self) -> None:
        if self._info is None:
            return
        self._info.set_config(self.cfg)
        self._info.set_session(self.state, self.session_id, self.started_at, self.last)

    # ---- 명령 ------------------------------------------------------------
    def handle(self, cmd: Command | None) -> PipelineParts | None:
        """명령을 처리하고, 부품이 바뀌었으면 새 PipelineParts를 돌려준다."""
        if cmd is None:
            return None
        if cmd.action == "stop":
            if self.state != "active":
                return None
            self._close()
            self.parts = self._idle_parts(self.cfg)
            self._publish()
            return self.parts

        cfg = self.cfg
        if cmd.exercise and cmd.exercise != cfg.exercise:
            try:
                cfg = self._load(cmd.exercise)
            except (FileNotFoundError, ValueError) as e:
                log.error("운동 전환 실패 (%s): %s", cmd.exercise, e)
                return None
        if cmd.action == "start":
            if self.state == "active" and cfg is self.cfg:
                return None  # 이미 이 운동으로 진행 중
        elif cfg is self.cfg:  # select: 같은 운동이면 할 일이 없다
            return None

        was_active = self.state == "active"
        if was_active:
            self._close()
        previous = self.cfg
        self.cfg = cfg
        if cmd.action == "start" or was_active:  # 운동 중에 운동을 바꾸면 새 운동으로 새 세션을 이어서 연다
            self._open(cfg)
            self.parts = self._active_parts(cfg)
        else:
            self.parts = self._idle_parts(cfg)
        if not cfg.calibrated:
            log.warning("'%s' 설정은 보정되지 않은 초기값입니다.", cfg.exercise)
        log.info("운동 전환: %s -> %s (%s)", previous.exercise, cfg.exercise, self.state)
        self._publish()
        return self.parts

    def shutdown(self) -> None:
        if self.state == "active":
            self._close()
        elif self.parts.recorder:
            self.parts.recorder.close()
