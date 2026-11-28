"""오버레이가 그려진 분석 화면을 MQTT 토픽으로 내보낸다 (백엔드/웹이 분석 화면을 볼 수 있게)."""
from __future__ import annotations

import json
import logging
import os
import time

import cv2
import numpy as np

from aipt_analyzer.config import ExerciseConfig
from aipt_analyzer.results import FrameResult
from aipt_analyzer.severity import check_severity
from aipt_analyzer.sources import parse_mqtt_spec

log = logging.getLogger("aipt.publisher")


class FramePublisher:
    """프레임을 JPEG로 인코딩해 발행한다. 브로커가 느리거나 끊기면 그 프레임은 건너뛴다(지연이 쌓이지 않게).

    인증이 필요하면 환경변수 MQTT_USERNAME / MQTT_PASSWORD.
    """

    def __init__(self, spec: str, quality: int = 70) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as e:
            raise RuntimeError("MQTT 발행에는 paho-mqtt가 필요합니다: pip install -e '.[mqtt]'") from e
        self._mqtt = mqtt
        host, port, self._topic, tls = parse_mqtt_spec(spec)
        self._params = [cv2.IMWRITE_JPEG_QUALITY, quality]
        self._pending = None  # 직전 발행. 아직 소켓에 못 나갔으면 이번 프레임은 버린다
        self.sent = self.dropped = 0
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if os.environ.get("MQTT_USERNAME"):
            self._client.username_pw_set(os.environ["MQTT_USERNAME"], os.environ.get("MQTT_PASSWORD"))
        if tls:
            self._client.tls_set()
        self._client.reconnect_delay_set(min_delay=1, max_delay=10)
        self._client.connect_async(host, port)
        self._client.loop_start()
        log.info("분석 화면 발행: %s:%d %s", host, port, self._topic)

    def publish(self, frame: np.ndarray) -> bool:
        if self._pending is not None and not self._pending.is_published():
            self.dropped += 1
            return False
        ok, buf = cv2.imencode(".jpg", frame, self._params)
        if not ok:
            return False
        info = self._client.publish(self._topic, buf.tobytes(), qos=0)
        if info.rc != self._mqtt.MQTT_ERR_SUCCESS:  # 브로커 연결이 끊긴 동안
            self._pending = None
            self.dropped += 1
            return False
        self._pending = info
        self.sent += 1
        return True

    def close(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()


INFO_VERSION = 3  # 2: checks(조건별 판정) 추가, 값은 소수 3자리 / 3: session(운동 세션 상태) 추가
MAX_REP_HISTORY = 12


class AnalysisPublisher:
    """프레임마다의 분석 결과를 JSON으로 줄여 MQTT로 내보낸다 (영상에 글자를 쓰지 않고 웹이 직접 그린다).

    초당 최대 rate_hz번만 보내되, 반복이 끝난 순간은 항상 보낸다. 받는 쪽은 가장 최근 값만 쓰면 된다.
    """

    def __init__(self, spec: str, cfg: ExerciseConfig, rate_hz: float = 10.0) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as e:
            raise RuntimeError("MQTT 발행에는 paho-mqtt가 필요합니다: pip install -e '.[mqtt]'") from e
        self._mqtt = mqtt
        host, port, self._topic, tls = parse_mqtt_spec(spec)
        self._cfg = cfg
        self._min_interval = 1.0 / rate_hz
        self._started = time.monotonic()
        self._last_sent = 0.0
        self._last_rep: dict | None = None
        self._rep_stats: dict[str, dict[str, float]] = {}  # 마지막 반복의 특징별 min/max/mean (반복 후 판정 조건용)
        self._scores: list[int] = []
        self._session = {"state": "active", "id": None, "started_at": None, "last": None}
        self.sent = 0
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if os.environ.get("MQTT_USERNAME"):
            self._client.username_pw_set(os.environ["MQTT_USERNAME"], os.environ.get("MQTT_PASSWORD"))
        if tls:
            self._client.tls_set()
        self._client.reconnect_delay_set(min_delay=1, max_delay=10)
        self._client.connect_async(host, port)
        self._client.loop_start()
        log.info("분석 정보 발행: %s:%d %s", host, port, self._topic)

    def set_config(self, cfg: ExerciseConfig) -> None:
        """운동이 바뀌었을 때: 새 설정으로 바꾸고 횟수·점수 이력·경과 시간을 처음부터 다시 센다."""
        self._cfg = cfg
        self._started = time.monotonic()
        self._last_rep = None
        self._rep_stats = {}
        self._scores = []

    def set_session(self, state: str, session_id: int | None, started_at, last: dict | None) -> None:
        """세션 상태: idle(준비, 횟수를 세지 않음) | active(운동 중). last는 방금 끝난 세션 요약."""
        self._session = {
            "state": state,
            "id": session_id,
            "started_at": started_at.isoformat() if started_at else None,
            "last": last,
        }
        self._started = time.monotonic()  # 경과 시간은 세션이 시작된 때부터 잰다
        self._last_rep = None
        self._rep_stats = {}
        self._scores = []

    def payload(self, result: FrameResult, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        cfg = self._cfg
        idle = self._session["state"] == "idle"  # 준비 중에는 자세만 보여 주고 횟수는 세지 않는다
        if result.rep is not None and not idle:
            r = result.rep
            self._last_rep = {
                "index": r.summary.index,
                "score": r.score,
                "feedback": list(r.feedback),
                "primary_min": round(r.summary.primary_min, 3),
                "duration": round(r.summary.duration, 2),
            }
            self._rep_stats = r.summary.stats
            self._scores = (self._scores + [r.score])[-MAX_REP_HISTORY:]
        value = result.features.get(cfg.primary_feature)
        return {
            "v": INFO_VERSION,
            "exercise": cfg.exercise,
            "calibrated": cfg.calibrated,
            "elapsed_seconds": round(now - self._started, 1),
            "visible": result.visible,
            "phase": result.phase,  # waiting | up | moving | down
            "reps": 0 if idle else result.reps,
            "session": self._session,
            "primary": {"feature": cfg.primary_feature, "value": None if value is None else round(value, 3)},
            "features": {k: round(v, 3) for k, v in result.features.items()},
            # 게이지에 표시할 기준선: 시작 자세 / 1회로 세는 지점 / 목표 깊이
            "thresholds": {"up": cfg.up_threshold, "count": cfg.count_threshold, "target": cfg.depth.target},
            "live_feedback": list(result.live_feedback),
            "checks": self._checks(result),
            "last_rep": self._last_rep,
            "rep_scores": list(self._scores),
        }

    def _checks(self, result: FrameResult) -> list[dict]:
        """켜져 있는 자세 조건별 현재 판정. 실시간(live) 조건은 지금 값으로, 반복 후 판정 조건은 마지막 반복 결과로 낸다.

        severity는 오버레이 관절선 색과 같은 기준(0 초록 ~ 1 빨강)이라 영상과 패널의 색이 어긋나지 않는다.
        state: ok | warn(기준에 다가감) | bad | pending(아직 판정할 값이 없음)
        """
        flagged = set(self._last_rep["feedback"]) if self._last_rep else set()
        out = []
        for c in self._cfg.form_checks:
            if not c.enabled:
                continue
            severity = None
            if c.live:
                value = result.features.get(c.feature)
                if value is None:
                    state = "pending"
                else:
                    severity = check_severity(c, value)
                    bad = value > c.limit if c.violates_above else value < c.limit
                    state = "bad" if bad else "warn" if severity >= 0.25 else "ok"
            else:
                value = self._rep_stats.get(c.feature, {}).get(c.stat) if self._last_rep else None
                if self._last_rep is None or value is None:
                    state = "pending"
                else:
                    severity = check_severity(c, value)
                    state = "bad" if c.code in flagged else "ok"  # 최종 판정은 판정기가 낸 피드백을 따른다
                    if state == "bad":
                        severity = max(severity, 0.5)
            out.append({
                "code": c.code,
                "feature": c.feature,
                "stat": c.stat,
                "direction": "above" if c.violates_above else "below",
                "limit": round(c.limit, 3),
                "live": c.live,
                "tier": c.tier,
                "value": None if value is None else round(value, 3),
                "severity": None if severity is None else round(severity, 3),
                "state": state,
            })
        return out

    def update(self, result: FrameResult) -> bool:
        now = time.monotonic()
        if result.rep is None and now - self._last_sent < self._min_interval:
            return False
        info = self._client.publish(self._topic, json.dumps(self.payload(result, now)).encode(), qos=0)
        if info.rc != self._mqtt.MQTT_ERR_SUCCESS:  # 브로커 연결이 끊긴 동안
            return False
        self._last_sent = now
        self.sent += 1
        return True

    def close(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()
