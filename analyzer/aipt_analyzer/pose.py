"""자세(키포인트) 표현과 기하 계산. 모델이 달라도 COCO 17점으로 맞춰 쓴다."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

COCO_KEYPOINTS = (
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
)  # fmt: skip
KEYPOINT_INDEX = {name: i for i, name in enumerate(COCO_KEYPOINTS)}

SKELETON_EDGES = (
    (5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4),
    (0, 5), (0, 6),
)  # fmt: skip


@dataclass(frozen=True)
class Pose:
    """한 사람의 COCO 17점. xy는 픽셀 좌표 (17, 2), conf는 신뢰도 (17,)."""

    xy: np.ndarray
    conf: np.ndarray

    def __post_init__(self) -> None:
        if self.xy.shape != (17, 2) or self.conf.shape != (17,):
            raise ValueError(f"expected xy (17, 2) and conf (17,), got {self.xy.shape}, {self.conf.shape}")

    @classmethod
    def from_rows(cls, rows) -> "Pose":
        """[[x, y, conf] * 17] 형태(기록 파일 형식)에서 만든다."""
        arr = np.asarray(rows, dtype=float)
        if arr.shape != (17, 3):
            raise ValueError(f"expected 17 rows of [x, y, conf], got shape {arr.shape}")
        return cls(arr[:, :2].copy(), arr[:, 2].copy())

    def to_rows(self) -> list[list[float]]:
        return [
            [round(float(x), 1), round(float(y), 1), round(float(c), 3)]
            for (x, y), c in zip(self.xy, self.conf)
        ]


def angle_deg(a, b, c) -> float | None:
    """점 b를 꼭짓점으로 하는 각 abc (0~180도). 점이 겹치면 None."""
    v1 = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    v2 = np.asarray(c, dtype=float) - np.asarray(b, dtype=float)
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return None
    cos = float(np.dot(v1, v2) / (n1 * n2))
    return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))


def vertical_lean_deg(base, top) -> float | None:
    """base->top 선분이 수직(위쪽)에서 벗어난 각도 (0~180도). 이미지 y축은 아래가 +."""
    v = np.asarray(top, dtype=float) - np.asarray(base, dtype=float)
    n = np.linalg.norm(v)
    if n < 1e-6:
        return None
    return float(np.degrees(np.arccos(np.clip(-v[1] / n, -1.0, 1.0))))
