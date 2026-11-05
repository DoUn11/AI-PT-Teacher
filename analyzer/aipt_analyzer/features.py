"""Pose -> 이름 붙은 특징(관절 각도 등).

특징은 운동과 무관하게 항상 같은 집합을 계산한다. 그래서 기록된 데이터로
임계값/모델을 학습할 때 운동이 달라도 같은 스키마를 쓸 수 있다.
새 특징은 FEATURES에 항목만 추가하면 된다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from aipt_analyzer.pose import KEYPOINT_INDEX, Pose, angle_deg, vertical_lean_deg

# 한쪽 몸통의 관절 이름 -> COCO 키포인트 이름
_SIDE_JOINTS = ("shoulder", "elbow", "wrist", "hip", "knee", "ankle")


@dataclass(frozen=True)
class FeatureDef:
    joints: tuple[str, ...]  # 필요한 관절 (위 _SIDE_JOINTS 이름)
    compute: Callable[[np.ndarray], float | None]  # (len(joints), 2) 좌표 -> 값


FEATURES: dict[str, FeatureDef] = {
    "knee_angle": FeatureDef(("hip", "knee", "ankle"), lambda p: angle_deg(p[0], p[1], p[2])),
    "hip_angle": FeatureDef(("shoulder", "hip", "knee"), lambda p: angle_deg(p[0], p[1], p[2])),
    "elbow_angle": FeatureDef(("shoulder", "elbow", "wrist"), lambda p: angle_deg(p[0], p[1], p[2])),
    "body_line": FeatureDef(("shoulder", "hip", "ankle"), lambda p: angle_deg(p[0], p[1], p[2])),
    "trunk_lean": FeatureDef(("hip", "shoulder"), lambda p: vertical_lean_deg(p[0], p[1])),
}


@dataclass(frozen=True)
class FrameFeatures:
    side: str  # "left" | "right": 특징을 계산한 몸의 쪽
    values: dict[str, float]


def _side_indices(side: str) -> dict[str, int]:
    return {j: KEYPOINT_INDEX[f"{side}_{j}"] for j in _SIDE_JOINTS}


def extract_features(pose: Pose, min_conf: float = 0.3) -> FrameFeatures:
    """더 잘 보이는 쪽 몸으로 특징을 계산한다. 관절이 안 보이는 특징은 빠진다."""
    best_side, best_idx, best_score = "left", _side_indices("left"), -1.0
    for side in ("left", "right"):
        idx = _side_indices(side)
        score = float(np.mean([pose.conf[i] for i in idx.values()]))
        if score > best_score:
            best_side, best_idx, best_score = side, idx, score

    values: dict[str, float] = {}
    for name, fdef in FEATURES.items():
        ids = [best_idx[j] for j in fdef.joints]
        if any(pose.conf[i] < min_conf for i in ids):
            continue
        value = fdef.compute(pose.xy[ids])
        if value is not None:
            values[name] = value
    return FrameFeatures(side=best_side, values=values)
