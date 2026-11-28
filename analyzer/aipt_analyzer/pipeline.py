"""영상 소스 -> 자세 추정 -> 분석 -> (표시 / 기록 / 업로드) 메인 루프."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from aipt_analyzer.analyzer import Analyzer
from aipt_analyzer.estimators import PoseEstimator
from aipt_analyzer.overlay import Overlay
from aipt_analyzer.recorder import SessionRecorder
from aipt_analyzer.results import FrameResult, RepResult
from aipt_analyzer.sources import FrameSource
from aipt_analyzer.uploader import UploadWorker

log = logging.getLogger("aipt")


@dataclass
class PipelineParts:
    """운동 하나에 묶인 처리 부품. 운동을 바꾸면 통째로 새로 만들어 넘긴다."""

    analyzer: Analyzer
    overlay: Overlay | None = None
    recorder: SessionRecorder | None = None
    worker: UploadWorker | None = None


def run_pipeline(
    analyzer: Analyzer,
    source: FrameSource,
    estimator: PoseEstimator,
    *,
    overlay: Overlay | None = None,
    recorder: SessionRecorder | None = None,
    worker: UploadWorker | None = None,
    on_rep: Callable[[RepResult], None] | None = None,
    on_annotated: Callable[[np.ndarray], None] | None = None,
    on_result: Callable[[FrameResult], None] | None = None,
    switch: Callable[[], PipelineParts | None] | None = None,
    show: bool = True,
    seconds: float = 0.0,
    window: str = "AI PT",
) -> int:
    """종료될 때까지 돌리고 완료한 rep 수를 돌려준다.

    overlay가 있으면 오버레이를 그린 프레임을 만든다. show면 글자까지 그려 창에 띄우고(q로 종료),
    on_annotated가 있으면 **글자 없이 관절선만** 그린 프레임을 넘긴다(웹으로 내보내기).
    on_result에는 프레임마다 분석 결과를 넘긴다(웹에 보여 줄 정보를 백엔드로 보내기).
    switch는 프레임마다 불리며, 새 PipelineParts를 돌려주면 그 부품으로 바꿔 이어서 처리한다(웹에서 운동 선택).
    이전 부품의 정리(기록·업로드 마무리)는 switch를 만든 쪽이 한다.
    """
    started = time.monotonic()
    while True:
        if switch is not None and (parts := switch()) is not None:
            analyzer, overlay, recorder, worker = parts.analyzer, parts.overlay, parts.recorder, parts.worker
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
            if on_result:
                on_result(result)
            if overlay:
                now = time.monotonic()
                if show:
                    cv2.imshow(window, overlay.render(frame, result, now))
                if on_annotated:
                    on_annotated(overlay.render(frame, result, now, text=False))

        if overlay and show and (cv2.waitKey(1) & 0xFF) == ord("q"):
            break
        if seconds and time.monotonic() - started >= seconds:
            break
    return analyzer.counter.reps
