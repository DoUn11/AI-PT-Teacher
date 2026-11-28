"""운동 세션의 생명주기와 세트 진행. 웹에서 목표(세트·횟수)를 정해 시작하면 분석기가 세트를 세고 쉬게 한다.

    idle(대기) --start--> [ready 5초] -> working(세트 1) -> resting -> working(세트 2) -> ... -> 마지막 세트 끝 -> idle + 세션 종료

- idle/ready/resting에서도 영상 분석과 관절선 색은 돌지만 횟수는 세지 않고 업로드도 안 한다 (세트 사이 움직임이 기록되지 않게).
- working이 시작될 때마다 분석기를 새로 만들되, 이미 한 횟수에서 이어 센다 (반복 번호가 세션 안에서 이어진다).
- 목표 없이 시작하면(plan=None) 예전처럼 세트 구분 없이 종료할 때까지 센다.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable

from aipt_analyzer.analyzer import Analyzer
from aipt_analyzer.config import ExerciseConfig, load_exercise_config
from aipt_analyzer.control import Command, Plan
from aipt_analyzer.overlay import Overlay
from aipt_analyzer.pipeline import PipelineParts
from aipt_analyzer.recorder import SessionRecorder
from aipt_analyzer.uploader import BackendClient, UploadWorker

log = logging.getLogger("aipt.station")

READY_SECONDS = 5.0  # 시작을 누른 뒤 자세를 잡을 시간


class Station:
    def __init__(
        self,
        cfg: ExerciseConfig,
        *,
        client: BackendClient | None = None,
        build_overlay: Callable[[ExerciseConfig], Overlay | None] = lambda cfg: None,
        record_dir: str | None = None,
        info=None,  # AnalysisPublisher (set_config / set_session / progress_source)
        autostart: bool = True,
        load_config: Callable[[str], ExerciseConfig] = load_exercise_config,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._build_overlay = build_overlay
        self._record_dir = record_dir
        self._info = info
        self._load = load_config
        self._clock = clock
        self.cfg = cfg
        self.state = "idle"
        self.session_id: int | None = None
        self.started_at: datetime | None = None
        self.last: dict | None = None  # 방금 끝난 세션 요약 (웹이 리포트로 이동하는 데 쓴다)
        self.plan: Plan | None = None
        self.set_index = 0
        self.reps_done = 0
        self.status = "working"  # working | ready | resting (state가 active일 때)
        self._until: float | None = None
        self._worker: UploadWorker | None = None
        self._recorder: SessionRecorder | None = None
        self.parts = self._quiet_parts()
        if info is not None:
            info.progress_source = self.progress
        if autostart:
            self._open(cfg, None)
            self.parts = self._working_parts()
        self._publish()

    # ---- 부품 ------------------------------------------------------------
    def _quiet_parts(self) -> PipelineParts:
        """횟수를 세지 않고 올리지도 않는 부품 (대기·준비·휴식). 이미 한 횟수에서 이어서 표시한다."""
        return PipelineParts(Analyzer(self.cfg, start_count=self.reps_done), self._build_overlay(self.cfg), None, None)

    def _working_parts(self) -> PipelineParts:
        return PipelineParts(Analyzer(self.cfg, start_count=self.reps_done), self._build_overlay(self.cfg), self._recorder, self._worker)

    def _open(self, cfg: ExerciseConfig, plan: Plan | None) -> None:
        self.cfg, self.plan = cfg, plan
        self.session_id = None
        if self._client is not None:
            try:
                self.session_id = self._client.start_session(cfg.exercise, cfg, plan)
            except Exception as e:  # 백엔드가 안 돼도 화면 분석은 계속한다
                log.error("세션 시작 실패, 업로드 없이 계속합니다: %s", e)
        self._worker = UploadWorker(self._client).start() if self._client is not None and self.session_id else None
        self._recorder = SessionRecorder(self._record_dir, cfg) if self._record_dir else None
        self.state = "active"
        self.started_at = datetime.now(timezone.utc)
        self.last = None
        self.reps_done = 0
        self.set_index = 1
        self.status = "working"
        self._until = None
        log.info("세션 시작: %s (id=%s, 목표=%s)", cfg.exercise, self.session_id, plan)

    def _close(self) -> None:
        """진행 중인 세션을 닫는다 (남은 반복 전송, 세션 종료, 기록 파일 닫기)."""
        finished = None
        if self._worker:
            self._worker.close()
            finished = self._client.last_finished if self._client is not None else None
        if self._recorder:
            self._recorder.close()
        sets_done = self._completed_sets()
        self.last = {
            "id": (finished or {}).get("id", self.session_id),
            "exercise": self.cfg.exercise,
            "total_reps": (finished or {}).get("total_reps", self.reps_done),
            "avg_score": (finished or {}).get("avg_score"),
            "saved": finished is not None,
            "target_sets": self.plan.sets if self.plan else None,
            "completed_sets": sets_done if self.plan else None,
        }
        log.info("세션 종료: %s", self.last)
        self.state = "idle"
        self.session_id = None
        self.started_at = None
        self.plan = None
        self.status = "working"
        self._until = None
        self._worker = self._recorder = None
        self.reps_done = 0
        self.set_index = 0

    def _completed_sets(self) -> int:
        if not self.plan:
            return 0
        return min(self.plan.sets, self.reps_done // self.plan.reps)

    def _publish(self) -> None:
        if self._info is None:
            return
        self._info.set_config(self.cfg)
        self._info.set_session(self.state, self.session_id, self.started_at, self.last)

    # ---- 세트 진행 ---------------------------------------------------------
    def progress(self) -> dict | None:
        """웹에 보여 줄 세트 진행. 목표 없이 운동 중이거나 대기 중이면 None."""
        if self.state != "active" or self.plan is None:
            return None
        p = self.plan
        if self.status == "working":
            self.reps_done = max(self.reps_done, self.parts.analyzer.counter.reps)
        remaining = None if self._until is None else max(0.0, self._until - self._clock())
        return {
            "sets": p.sets, "reps": p.reps, "rest_seconds": p.rest_seconds,
            "set_index": self.set_index,
            "set_reps": max(0, min(p.reps, self.reps_done - (self.set_index - 1) * p.reps)),
            "reps_done": self.reps_done,
            "status": self.status,
            "remaining": None if remaining is None else round(remaining, 1),
        }

    def _begin_set(self) -> PipelineParts:
        self.status, self._until = "working", None
        self.parts = self._working_parts()
        self._publish()
        return self.parts

    def _advance(self) -> PipelineParts | None:
        """세트가 끝났는지, 쉬는/준비 시간이 지났는지 본다. 부품이 바뀌면 새 PipelineParts를 돌려준다."""
        if self.state != "active" or self.plan is None:
            return None
        if self.status == "working":
            self.reps_done = max(self.reps_done, self.parts.analyzer.counter.reps)
            if self.reps_done < self.set_index * self.plan.reps:
                return None
            if self.set_index >= self.plan.sets:  # 마지막 세트까지 끝: 세션을 닫고 리포트로 넘긴다
                log.info("목표 완료: %d세트 x %d회", self.plan.sets, self.plan.reps)
                self._close()
                self.parts = self._quiet_parts()
                self._publish()
                return self.parts
            if self.plan.rest_seconds <= 0:
                self.set_index += 1
                return self._begin_set()
            self.status, self._until = "resting", self._clock() + self.plan.rest_seconds
            self.parts = self._quiet_parts()
            self._publish()
            return self.parts
        if self._until is not None and self._clock() >= self._until:
            if self.status == "resting":
                self.set_index += 1
            return self._begin_set()
        return None

    # ---- 명령 ------------------------------------------------------------
    def step(self, cmd: Command | None) -> PipelineParts | None:
        """프레임마다 부른다. 명령을 처리하고 세트 진행을 한 칸 나아가게 한다. 부품이 바뀌면 새 PipelineParts."""
        changed = self.handle(cmd)
        return self._advance() or changed

    def handle(self, cmd: Command | None) -> PipelineParts | None:
        if cmd is None:
            return None
        if cmd.action == "stop":
            if self.state != "active":
                return None
            self._close()
            self.parts = self._quiet_parts()
            self._publish()
            return self.parts
        if cmd.action == "skip_rest":
            if self.state == "active" and self.status in ("ready", "resting"):
                self._until = self._clock()  # 다음 step에서 바로 다음 세트가 시작된다
            return None
        if self.state == "active":  # 운동 중에는 운동을 바꾸거나 다시 시작하지 않는다 (먼저 종료)
            log.warning("운동 중이라 %s 요청을 무시합니다", cmd.action)
            return None

        cfg = self.cfg
        if cmd.exercise and cmd.exercise != cfg.exercise:
            try:
                cfg = self._load(cmd.exercise)
            except (FileNotFoundError, ValueError) as e:
                log.error("운동 전환 실패 (%s): %s", cmd.exercise, e)
                return None
        if not cfg.calibrated:
            log.warning("'%s' 설정은 보정되지 않은 초기값입니다.", cfg.exercise)

        if cmd.action == "select":
            if cfg is self.cfg:
                return None
            self.cfg = cfg
            self.parts = self._quiet_parts()
            self._publish()
            return self.parts

        # start
        self._open(cfg, cmd.plan)
        if cmd.plan is not None:  # 자세를 잡을 시간을 준다
            self.status, self._until = "ready", self._clock() + READY_SECONDS
            self.parts = self._quiet_parts()
        else:
            self.parts = self._working_parts()
        self._publish()
        return self.parts

    def shutdown(self) -> None:
        if self.state == "active":
            self._close()
        elif self._recorder:
            self._recorder.close()
