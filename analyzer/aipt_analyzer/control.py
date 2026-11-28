"""웹에서 고른 운동을 MQTT 제어 토픽으로 받아 실행 중에 운동을 바꾼다."""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import NamedTuple

from aipt_analyzer.config import available_exercises
from aipt_analyzer.sources import parse_mqtt_spec

log = logging.getLogger("aipt.control")


class Command(NamedTuple):
    action: str  # select(운동만 고름) | start(세션 시작) | stop(세션 종료)
    exercise: str | None = None


class ExerciseControl:
    """제어 토픽을 구독하고, 가장 최근 명령 하나를 보관한다.

        {"exercise": "kneeup"}                      운동 고르기 (select)
        {"action": "start", "exercise": "kneeup"}   세션 시작 (exercise 생략하면 지금 운동)
        {"action": "stop"}                          세션 종료

    영상 루프가 take()로 가져가 처리한다. 모르는 운동/동작이나 깨진 메시지는 무시한다.
    인증이 필요하면 환경변수 MQTT_USERNAME / MQTT_PASSWORD.
    """

    def __init__(self, spec: str) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as e:
            raise RuntimeError("MQTT 제어에는 paho-mqtt가 필요합니다: pip install -e '.[mqtt]'") from e
        host, port, self._topic, tls = parse_mqtt_spec(spec)
        self._lock = threading.Lock()
        self._pending: Command | None = None
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if os.environ.get("MQTT_USERNAME"):
            self._client.username_pw_set(os.environ["MQTT_USERNAME"], os.environ.get("MQTT_PASSWORD"))
        if tls:
            self._client.tls_set()
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=10)
        self._client.connect_async(host, port)
        self._client.loop_start()
        log.info("운동 선택 제어 수신: %s:%d %s", host, port, self._topic)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code == 0:
            client.subscribe(self._topic, qos=0)  # 재연결마다 다시 구독

    def _on_message(self, client, userdata, msg) -> None:
        if msg.retain:  # 예전에 남은 요청으로 시작하자마자 운동이 바뀌지 않게 한다
            return
        try:
            data = json.loads(msg.payload)
            action = data.get("action", "select")
            name = data.get("exercise")
        except (ValueError, AttributeError, TypeError):
            log.warning("제어 메시지 형식 오류, 무시")
            return
        if action not in ("select", "start", "stop") or (action == "select" and name is None):
            log.warning("지원하지 않는 제어 요청, 무시: %r", data)
            return
        if name is not None and name not in available_exercises():
            log.warning("지원하지 않는 운동 요청, 무시: %r", name)
            return
        with self._lock:
            self._pending = Command(action, name)

    def take(self) -> Command | None:
        with self._lock:
            name, self._pending = self._pending, None
            return name

    def close(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()
