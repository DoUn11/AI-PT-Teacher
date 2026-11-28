"""Pi 카메라 프레임을 MQTT로 받아 최신 한 장과 수신 상태만 들고 있는다.

프레임은 저장하지 않는다(메모리에 최신 1장). 상태가 바뀔 때만 on_event 콜백으로 알려
DB에 기록한다.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Callable
from urllib.parse import urlparse

from pydantic import ValidationError

from app.modules.camera.schemas import AnalysisInfo

log = logging.getLogger("aipt.camera")

FPS_WINDOW_SECONDS = 5.0
_JPEG_MAGIC = b"\xff\xd8"


class CameraHub:
    def __init__(
        self,
        offline_after: float = 5.0,
        on_event: Callable[[str, str | None], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        event_names: tuple[str, str] = ("stream_online", "stream_offline"),
    ) -> None:
        self.offline_after = offline_after
        self.on_event = on_event
        self._event_online, self._event_offline = event_names
        self._clock = clock
        self._lock = threading.Lock()
        self.enabled = False
        self._frame: bytes | None = None
        self._seq = 0
        self._last_frame_at: float | None = None
        self._arrivals: deque[float] = deque()
        self._online = False
        self._broker_connected = False

    def _emit(self, event: str, detail: str | None = None) -> None:
        log.info("camera event: %s %s", event, detail or "")
        if self.on_event:
            try:
                self.on_event(event, detail)
            except Exception:  # 기록 실패가 수신을 멈추면 안 된다
                log.exception("camera event 기록 실패")

    def on_frame(self, payload: bytes) -> None:
        if not payload.startswith(_JPEG_MAGIC):
            return
        now = self._clock()
        with self._lock:
            self._frame = payload
            self._seq += 1
            self._last_frame_at = now
            self._arrivals.append(now)
            went_online = not self._online
            self._online = True
        if went_online:
            self._emit(self._event_online)

    def tick(self) -> None:
        """주기적으로 호출. 프레임이 offline_after초 넘게 안 오면 오프라인으로 바꾼다."""
        with self._lock:
            silent = (
                self._online
                and self._last_frame_at is not None
                and self._clock() - self._last_frame_at > self.offline_after
            )
            if silent:
                self._online = False
        if silent:
            self._emit(self._event_offline, f"{self.offline_after:g}초 이상 프레임 없음")

    def set_broker_connected(self, connected: bool, detail: str | None = None) -> None:
        with self._lock:
            changed = self._broker_connected != connected
            self._broker_connected = connected
        if changed:
            self._emit("broker_connected" if connected else "broker_disconnected", detail)

    def latest(self) -> tuple[bytes | None, int]:
        with self._lock:
            return self._frame, self._seq

    def status(self) -> dict:
        now = self._clock()
        with self._lock:
            while self._arrivals and now - self._arrivals[0] > FPS_WINDOW_SECONDS:
                self._arrivals.popleft()
            fps = len(self._arrivals) / FPS_WINDOW_SECONDS
            age = None if self._last_frame_at is None else now - self._last_frame_at
            return {
                "enabled": self.enabled,
                "broker_connected": self._broker_connected,
                "online": self._online,
                "fps": round(fps, 1),
                "last_frame_age_seconds": None if age is None else round(age, 1),
                "frames_total": self._seq,
            }


class InfoHub:
    """analyzer가 보낸 분석 정보(JSON) 중 가장 최근 것만 들고 있는다."""

    def __init__(self, offline_after: float = 5.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.offline_after = offline_after
        self._clock = clock
        self._lock = threading.Lock()
        self._info: AnalysisInfo | None = None
        self._at: float | None = None

    def on_info(self, payload: bytes) -> None:
        try:
            info = AnalysisInfo.model_validate_json(payload)
        except ValidationError:  # 형식이 다른 메시지는 무시 (analyzer 버전이 다를 때 등)
            log.debug("분석 정보 형식 오류, 무시")
            return
        with self._lock:
            self._info, self._at = info, self._clock()

    def snapshot(self) -> dict:
        with self._lock:
            if self._info is None or self._at is None:
                return {"online": False, "age_seconds": None, "info": None}
            age = self._clock() - self._at
            return {"online": age <= self.offline_after, "age_seconds": round(age, 1), "info": self._info}


# 원본(Pi가 보낸 프레임)과 분석 화면(analyzer가 오버레이를 그려 다시 내보낸 프레임)을 따로 받는다.
hub = CameraHub()
analyzed_hub = CameraHub(event_names=("analysis_online", "analysis_offline"))
info_hub = InfoHub()
# 서버가 시작될 때 MQTT가 켜져 있으면 연결된 클라이언트가 들어온다 (웹 -> analyzer 운동 선택을 보낼 때 쓴다)
control: "MqttCameraClient | None" = None


class MqttCameraClient:
    """브로커에 접속해 프레임 토픽을 구독하고 hub에 넘긴다. 끊기면 paho가 재연결한다."""

    def __init__(
        self,
        hub: CameraHub,
        url: str,
        topic: str,
        analyzed_hub: CameraHub | None = None,
        analyzed_topic: str | None = None,
        info_hub: InfoHub | None = None,
        info_topic: str | None = None,
        control_topic: str | None = None,
    ) -> None:
        import paho.mqtt.client as mqtt  # 선택 의존성: pip install -e ".[mqtt]"

        u = urlparse(url)
        if u.scheme not in ("mqtt", "mqtts") or not u.hostname:
            raise ValueError(f"MQTT_URL 형식이 아닙니다: {url} (예: mqtt://localhost:1883)")
        self._hub, self._topic = hub, topic
        # 토픽 -> 메시지를 받을 곳. 같은 토픽을 두 번 구독하지 않도록 dict로 둔다.
        self._routes: dict[str, Callable[[bytes], None]] = {topic: hub.on_frame}
        self._ticks: list[Callable[[], None]] = [hub.tick]
        if analyzed_hub is not None and analyzed_topic:
            self._routes[analyzed_topic] = analyzed_hub.on_frame
            self._ticks.append(analyzed_hub.tick)
        if info_hub is not None and info_topic:
            self._routes[info_topic] = info_hub.on_info
        self._host = u.hostname
        tls = u.scheme == "mqtts"
        self._port = u.port or (8883 if tls else 1883)
        self._control_topic = control_topic
        self._stop = threading.Event()
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        if tls:
            self._client.tls_set()
        self._client.reconnect_delay_set(min_delay=1, max_delay=10)
        self._watchdog = threading.Thread(target=self._watch, daemon=True)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code != 0:
            log.error("MQTT 연결 거부: %s", reason_code)
            return
        for topic in self._routes:
            client.subscribe(topic, qos=0)  # 재연결마다 다시 구독
        self._hub.set_broker_connected(True, f"{self._host}:{self._port} {self._topic}")

    def _on_message(self, client, userdata, msg) -> None:
        handler = self._routes.get(msg.topic)
        if handler is not None:
            handler(msg.payload)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None) -> None:
        self._hub.set_broker_connected(False, str(reason_code))

    def _watch(self) -> None:
        while not self._stop.wait(1.0):
            for tick in self._ticks:
                tick()

    def publish_control(self, command: dict) -> bool:
        """analyzer에 제어 명령을 보낸다 (QoS 1). 브로커에 못 보냈으면 False."""
        if not self._control_topic:
            return False
        info = self._client.publish(self._control_topic, json.dumps(command), qos=1)
        return info.rc == 0  # MQTT_ERR_SUCCESS

    def publish_exercise(self, exercise: str) -> bool:
        """웹에서 고른 운동을 analyzer에 알린다."""
        return self.publish_control({"exercise": exercise})

    def start(self) -> None:
        self._hub.enabled = True
        self._client.connect_async(self._host, self._port)  # 브로커가 늦게 떠도 계속 재시도
        self._client.loop_start()
        self._watchdog.start()

    def stop(self) -> None:
        self._stop.set()
        self._client.disconnect()
        self._client.loop_stop()
        self._hub.enabled = False
