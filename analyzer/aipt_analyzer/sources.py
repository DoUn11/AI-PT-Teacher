"""영상 소스: Pi 스트림(tcp://...), Pi MQTT(mqtt://...), 웹캠 번호, 영상 파일."""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Protocol
from urllib.parse import urlparse

import cv2
import numpy as np


class FrameSource(Protocol):
    exhausted: bool

    def read(self, timeout: float = 1.0) -> tuple[np.ndarray, float] | None:
        """(프레임, 시각[초]). 새 프레임이 없으면 None (exhausted가 True면 끝)."""
        ...

    def close(self) -> None: ...


class FileSource:
    """영상 파일을 순서대로 읽는다. 시각은 파일 안의 프레임 시간."""

    def __init__(self, path: str) -> None:
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise FileNotFoundError(f"cannot open video: {path}")
        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._idx = 0
        self.exhausted = False

    def read(self, timeout: float = 1.0) -> tuple[np.ndarray, float] | None:
        ok, frame = self._cap.read()
        if not ok:
            self.exhausted = True
            return None
        t = self._idx / self._fps
        self._idx += 1
        return frame, t

    def close(self) -> None:
        self._cap.release()


class _LatestFrameSource:
    """생산 스레드가 넣은 프레임 중 가장 최근 것만 소비자에게 주는 공통 부분.

    추론이 느릴 때 오래된 프레임이 쌓여 지연이 커지는 것을 막는다.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: tuple[np.ndarray, float] | None = None
        self._seq = 0
        self._seen = 0
        self._stop = threading.Event()
        self.exhausted = False

    def _push(self, frame: np.ndarray) -> None:
        with self._cond:
            self._frame = (frame, time.monotonic())
            self._seq += 1
            self._cond.notify_all()

    def read(self, timeout: float = 1.0) -> tuple[np.ndarray, float] | None:
        with self._cond:
            self._cond.wait_for(lambda: self._seq != self._seen or self._stop.is_set(), timeout)
            if self._seq == self._seen:
                return None
            self._seen = self._seq
            return self._frame

    def close(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()


class LiveSource(_LatestFrameSource):
    """스트림/웹캠을 별도 스레드로 계속 읽고, 소비자에게는 항상 가장 최근 프레임만 준다.

    끊기면 자동으로 다시 연결한다.
    """

    def __init__(self, spec: str | int, reopen_delay: float = 1.0) -> None:
        super().__init__()
        self._spec = spec
        self._reopen_delay = reopen_delay
        if isinstance(spec, str):
            # FFmpeg 내부 버퍼를 줄인다 (이미 설정돼 있으면 건드리지 않음)
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "fflags;nobuffer|flags;low_delay")
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        cap = None
        while not self._stop.is_set():
            if cap is None:
                cap = cv2.VideoCapture(self._spec)
                if not cap.isOpened():
                    cap.release()
                    cap = None
                    self._stop.wait(self._reopen_delay)
                    continue
            ok, frame = cap.read()
            if not ok:
                cap.release()
                cap = None
                self._stop.wait(self._reopen_delay)
                continue
            self._push(frame)
        if cap is not None:
            cap.release()

    def close(self) -> None:
        super().close()
        self._thread.join(timeout=2.0)


DEFAULT_MQTT_TOPIC = "aipt/camera/frame"


def parse_mqtt_spec(spec: str) -> tuple[str, int, str, bool]:
    """'mqtt://host[:port][/topic]' (TLS는 mqtts://) -> (host, port, topic, tls)."""
    u = urlparse(spec)
    if u.scheme not in ("mqtt", "mqtts") or not u.hostname:
        raise ValueError(f"MQTT 주소 형식이 아닙니다: {spec} (예: mqtt://192.168.1.101:1883/aipt/camera/frame)")
    tls = u.scheme == "mqtts"
    return u.hostname, u.port or (8883 if tls else 1883), u.path.lstrip("/") or DEFAULT_MQTT_TOPIC, tls


class MqttSource(_LatestFrameSource):
    """MQTT 토픽으로 들어오는 JPEG 프레임을 받는다 (Pi는 raspberry/pi_mqtt_publisher.py).

    브로커 연결이 끊기면 paho가 자동으로 다시 연결하고 구독도 다시 한다.
    인증이 필요하면 환경변수 MQTT_USERNAME / MQTT_PASSWORD (명령줄/주소에 넣으면 기록에 남는다).
    """

    def __init__(self, spec: str, report_interval: float = 5.0) -> None:
        super().__init__()
        try:
            import paho.mqtt.client as mqtt
        except ImportError as e:
            raise RuntimeError("MQTT 소스에는 paho-mqtt가 필요합니다: pip install -e '.[mqtt]'") from e
        self._host, port, self._topic, tls = parse_mqtt_spec(spec)
        self._log = logging.getLogger("aipt.mqtt")
        self._report_interval = report_interval
        self._stats_lock = threading.Lock()
        self._rx_frames = self._rx_bytes = self._rx_bad = 0  # 마지막 보고 이후 누적
        self._connected = False
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if os.environ.get("MQTT_USERNAME"):
            self._client.username_pw_set(os.environ["MQTT_USERNAME"], os.environ.get("MQTT_PASSWORD"))
        if tls:
            self._client.tls_set()
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=10)
        self._client.connect_async(self._host, port)  # 브로커가 아직 없어도 계속 재시도한다
        self._client.loop_start()
        self._reporter = threading.Thread(target=self._report_loop, daemon=True)
        self._reporter.start()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code != 0:
            self._log.error("MQTT 연결 거부: %s", reason_code)
            return
        self._connected = True
        self._log.info("MQTT 연결됨: %s, 구독 %s", self._host, self._topic)
        client.subscribe(self._topic, qos=0)  # 재연결 때마다 다시 구독

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None) -> None:
        self._connected = False
        if not self._stop.is_set():
            self._log.warning("MQTT 연결 끊김 (%s). 다시 연결합니다.", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        frame = cv2.imdecode(np.frombuffer(msg.payload, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:  # 깨진/JPEG가 아닌 메시지는 건너뛴다
            with self._stats_lock:
                self._rx_bad += 1
            return
        if self._seq == 0:
            self._log.info("첫 프레임 수신 %dx%d", frame.shape[1], frame.shape[0])
        with self._stats_lock:
            self._rx_frames += 1
            self._rx_bytes += len(msg.payload)
        self._push(frame)

    def _report_loop(self) -> None:
        """수신 상태를 주기적으로 알려 준다. 프레임이 끊기면 원인 후보를 같이 알려 준다."""
        silent = 0.0
        while not self._stop.wait(self._report_interval):
            with self._stats_lock:
                n, size, bad = self._rx_frames, self._rx_bytes, self._rx_bad
                self._rx_frames = self._rx_bytes = self._rx_bad = 0
            if n:
                silent = 0.0
                self._log.info("수신 중: %.1f fps, %.0f KB/s%s", n / self._report_interval,
                               size / 1024 / self._report_interval,
                               f", 디코딩 실패 {bad}건" if bad else "")
                continue
            silent += self._report_interval
            if not self._connected:
                self._log.warning("브로커(%s)에 연결되지 않았습니다 (%.0f초째). 브로커 실행/주소를 확인하세요.",
                                  self._host, silent)
            elif bad:
                self._log.warning("메시지는 오지만 JPEG이 아닙니다 (%d건). 토픽 %s의 페이로드를 확인하세요.",
                                  bad, self._topic)
            else:
                self._log.warning("브로커에는 연결됐지만 프레임이 없습니다 (%.0f초째). "
                                  "Pi 송신이 켜져 있는지, 토픽이 %s 와 같은지 확인하세요.", silent, self._topic)

    def close(self) -> None:
        super().close()
        self._reporter.join(timeout=2.0)
        self._client.disconnect()
        self._client.loop_stop()


def open_source(spec: str) -> FrameSource:
    if spec.isdigit():
        return LiveSource(int(spec))
    if spec.startswith(("mqtt://", "mqtts://")):
        return MqttSource(spec)
    if "://" in spec:
        return LiveSource(spec)
    return FileSource(spec)
