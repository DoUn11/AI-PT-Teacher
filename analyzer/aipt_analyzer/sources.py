"""영상 소스: Pi 스트림(tcp://...), 웹캠 번호, 영상 파일."""
from __future__ import annotations

import os
import threading
import time
from typing import Protocol

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


class LiveSource:
    """스트림/웹캠을 별도 스레드로 계속 읽고, 소비자에게는 항상 가장 최근 프레임만 준다.

    추론이 느릴 때 오래된 프레임이 쌓여 지연이 커지는 것을 막는다.
    끊기면 자동으로 다시 연결한다.
    """

    def __init__(self, spec: str | int, reopen_delay: float = 1.0) -> None:
        self._spec = spec
        self._reopen_delay = reopen_delay
        self._cond = threading.Condition()
        self._frame: tuple[np.ndarray, float] | None = None
        self._seq = 0
        self._seen = 0
        self._stop = threading.Event()
        self.exhausted = False
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
            with self._cond:
                self._frame = (frame, time.monotonic())
                self._seq += 1
                self._cond.notify_all()
        if cap is not None:
            cap.release()

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
        self._thread.join(timeout=2.0)


def open_source(spec: str) -> FrameSource:
    if spec.isdigit():
        return LiveSource(int(spec))
    if "://" in spec:
        return LiveSource(spec)
    return FileSource(spec)
