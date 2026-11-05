"""영상 소스 -> 자세 추정 -> 분석 -> (표시 / 기록 / 업로드) 메인 루프."""
from __future__ import annotations

import logging
import time
from typing import Callable

import cv2

from aipt_analyzer.analyzer import Analyzer
from aipt_analyzer.estimators import PoseEstimator
from aipt_analyzer.overlay import Overlay
from aipt_analyzer.recorder import SessionRecorder
from aipt_analyzer.results import RepResult
from aipt_analyzer.sources import FrameSource
from aipt_analyzer.uploader import UploadWorker

log = logging.getLogger("aipt")


def run_pipeline(
    analyzer: Analyzer,
    source: FrameSource,
    estimator: PoseEstimator,
    *,
    overlay: Overlay | None = None,
    recorder: SessionRecorder | None = None,
    worker: UploadWorker | None = None,
    on_rep: Callable[[RepResult], None] | None = None,
    seconds: float = 0.0,
    window: str = "AI PT",
) -> int:
    """종료될 때까지 돌리고 완료한 rep 수를 돌려준다. overlay가 있으면 창을 띄우고 q로 종료."""
    started = time.monotonic()
    while True:
        item = source.read()
        if item is None:
            if source.exhausted:
                break
        else:
            frame, t = item
            result = analyzer.process(estimator.estimate(frame), t)

            if result.rep is not None:
                log.info("rep %d 완료: %d점 %s", result.rep.summary.index, result.rep.score,
                         list(result.rep.feedback) or "")
                if recorder:
                    recorder.rep(result.rep)
                if worker:
                    worker.submit(result.rep)
                if on_rep:
                    on_rep(result.rep)
            if recorder:
                recorder.frame(t, result)
            if overlay:
                cv2.imshow(window, overlay.render(frame, result, time.monotonic()))

        if overlay and (cv2.waitKey(1) & 0xFF) == ord("q"):
            break
        if seconds and time.monotonic() - started >= seconds:
            break
    return analyzer.counter.reps
