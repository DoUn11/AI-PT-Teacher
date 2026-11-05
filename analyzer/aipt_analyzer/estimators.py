"""자세 추정기. Pose만 돌려주면 어떤 모델이든 꽂을 수 있다 (RTMPose 등으로 교체 가능)."""
from __future__ import annotations

from typing import Callable, Protocol

import numpy as np

from aipt_analyzer.pose import Pose


class PoseEstimator(Protocol):
    def estimate(self, frame_bgr: np.ndarray) -> Pose | None:
        """BGR 프레임에서 한 사람(가장 크게 보이는 사람)의 Pose. 없으면 None."""
        ...


EstimatorFactory = Callable[..., PoseEstimator]
_ESTIMATORS: dict[str, EstimatorFactory] = {}


def register_estimator(name: str) -> Callable[[EstimatorFactory], EstimatorFactory]:
    def deco(factory: EstimatorFactory) -> EstimatorFactory:
        _ESTIMATORS[name] = factory
        return factory

    return deco


def create_estimator(name: str, **kwargs) -> PoseEstimator:
    try:
        factory = _ESTIMATORS[name]
    except KeyError:
        raise ValueError(f"unknown estimator {name!r}; registered: {sorted(_ESTIMATORS)}") from None
    return factory(**kwargs)


def _to_numpy(x) -> np.ndarray:
    return x.cpu().numpy() if hasattr(x, "cpu") else np.asarray(x)


def pose_from_yolo_result(result) -> Pose | None:
    """ultralytics 결과 하나에서 가장 큰 사람의 Pose를 뽑는다."""
    kps = result.keypoints
    if kps is None or len(kps.xy) == 0:
        return None
    xy = _to_numpy(kps.xy)  # (n, 17, 2)
    conf = _to_numpy(kps.conf) if kps.conf is not None else np.ones(xy.shape[:2])
    boxes = _to_numpy(result.boxes.xyxy)  # (n, 4)
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    i = int(np.argmax(areas))
    return Pose(xy[i].astype(float), conf[i].astype(float))


@register_estimator("yolo")
class YoloPoseEstimator:
    def __init__(self, model: str = "yolo11n-pose.pt", device: str | None = None, conf: float = 0.25):
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise RuntimeError(
                'ultralytics가 필요합니다: pip install -e ".[yolo]"'
            ) from e
        self._model = YOLO(model)
        self._device = device
        self._conf = conf
        self._model(np.zeros((480, 640, 3), np.uint8), verbose=False)  # 첫 추론 지연 제거

    def estimate(self, frame_bgr: np.ndarray) -> Pose | None:
        result = self._model(frame_bgr, verbose=False, conf=self._conf, device=self._device)[0]
        return pose_from_yolo_result(result)
