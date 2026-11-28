"""Pose -> 이름 붙은 특징(관절 각도 등).

특징은 운동과 무관하게 항상 같은 집합을 계산한다. 그래서 기록된 데이터로
임계값/모델을 학습할 때 운동이 달라도 같은 스키마를 쓸 수 있다.
새 특징은 FEATURES(한쪽 몸) 또는 BODY_FEATURES(양쪽 몸·얼굴)에 항목만 추가하면 된다.

BODY_FEATURES는 정면 촬영용이다. 정의는 analyzer/calibration/extract_standing.py,
extract_pushup.py(임계값을 구한 데이터 분석)와 같고, frontend/src/analysis.js와도 같아야 한다.
이미지 좌표(y가 아래쪽) 기준이며, 길이는 몸통 길이(어깨 중점-골반 중점)로 나눠 화면 크기와 무관하다.
"들어 올린 다리"는 무릎이 골반보다 더 높이 올라온 쪽이다.
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


_UP = np.array([0.0, -1.0])


def _unit(v: np.ndarray) -> np.ndarray | None:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-6 else None


def _mean_or_none(*xs: float | None) -> float | None:
    v = [x for x in xs if x is not None]
    return sum(v) / len(v) if v else None


def _body(p: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray, float] | None:
    S = (p["left_shoulder"] + p["right_shoulder"]) / 2
    H = (p["left_hip"] + p["right_hip"]) / 2
    torso = float(np.linalg.norm(S - H))
    return (S, H, torso) if torso > 1e-6 else None


def _knee_h(p, side: str, torso: float) -> float:
    return float(np.dot(p[f"{side}_knee"] - p[f"{side}_hip"], _UP)) / torso


def _lifted(p, torso: float) -> str:
    return "left" if _knee_h(p, "left", torso) >= _knee_h(p, "right", torso) else "right"


def _torso_sw(p):
    b = _body(p)
    sw = float(np.linalg.norm(p["left_shoulder"] - p["right_shoulder"]))
    return b[2] / sw if b and sw > 1e-6 else None


def _knee_gap(p):
    """골반이 들어 올린 쪽 무릎보다 얼마나 위에 있나 / 몸통. 서 있으면 크고(약 0.75), 무릎을 들면 작아진다(rep 기준)."""
    b = _body(p)
    return -_knee_h(p, _lifted(p, b[2]), b[2]) if b else None


def _hip_flex(p):
    b = _body(p)
    if not b:
        return None
    s = _lifted(p, b[2])
    return angle_deg(p[f"{s}_shoulder"], p[f"{s}_hip"], p[f"{s}_knee"])


def _knee_lat(p):
    """들어 올린 무릎이 골반에서 바깥(골반선 방향)으로 벗어난 정도 / 몸통."""
    b = _body(p)
    if not b:
        return None
    s = _lifted(p, b[2])
    o = "right" if s == "left" else "left"
    out = _unit(p[f"{s}_hip"] - p[f"{o}_hip"])
    return float(np.dot(p[f"{s}_knee"] - p[f"{s}_hip"], out)) / b[2] if out is not None else None


def _ankle_lift(p):
    b = _body(p)
    return abs(float(np.dot(p["left_ankle"] - p["right_ankle"], _UP))) / b[2] if b else None


def _head(p):
    b = _body(p)
    E = (p["left_ear"] + p["right_ear"]) / 2
    return (p["nose"] - E, b) if b else (None, None)


def _head_pitch(p):
    """(코 - 귀 중점)의 위쪽 성분 / 몸통. 고개를 숙이면 작아진다."""
    v, b = _head(p)
    return float(np.dot(v, _UP)) / b[2] if b else None


def _head_yaw(p):
    v, b = _head(p)
    axis = _unit(p["left_shoulder"] - p["right_shoulder"])
    return abs(float(np.dot(v, axis))) / b[2] if b and axis is not None else None


def _elbow_angle_both(p):
    return _mean_or_none(*(angle_deg(p[f"{s}_shoulder"], p[f"{s}_elbow"], p[f"{s}_wrist"]) for s in ("left", "right")))


def _shoulder_angle_both(p):
    return _mean_or_none(*(angle_deg(p[f"{s}_hip"], p[f"{s}_shoulder"], p[f"{s}_elbow"]) for s in ("left", "right")))


def _hand_ratio(p):
    sw = float(np.linalg.norm(p["left_shoulder"] - p["right_shoulder"]))
    return float(np.linalg.norm(p["left_wrist"] - p["right_wrist"])) / sw if sw > 1e-6 else None


def _hand_out(p):
    """손목이 같은 쪽 어깨보다 바깥(어깨 중점 반대쪽)으로 나간 가로 거리 / 어깨 너비. 양손 중 큰 값."""
    mid = (p["left_shoulder"] + p["right_shoulder"]) / 2
    sw = float(np.linalg.norm(p["left_shoulder"] - p["right_shoulder"]))
    if sw < 1e-6:
        return None
    outs = []
    for s in ("left", "right"):
        out = (p[f"{s}_shoulder"] - mid) / (sw / 2)
        outs.append(float(np.dot(p[f"{s}_wrist"] - p[f"{s}_shoulder"], out)) / sw)
    return max(outs)


def _both(*joints: str) -> tuple[str, ...]:
    return tuple(f"{s}_{j}" for s in ("left", "right") for j in joints)


_TRUNK = _both("shoulder", "hip")


@dataclass(frozen=True)
class BodyFeatureDef:
    keypoints: tuple[str, ...]  # 필요한 COCO 키포인트 이름 (모두 보여야 계산)
    compute: Callable[[dict[str, np.ndarray]], float | None]


BODY_FEATURES: dict[str, BodyFeatureDef] = {
    "torso_sw": BodyFeatureDef(_TRUNK, _torso_sw),
    "knee_gap": BodyFeatureDef(_TRUNK + _both("knee"), _knee_gap),
    "hip_flex": BodyFeatureDef(_TRUNK + _both("knee"), _hip_flex),
    "knee_lat": BodyFeatureDef(_TRUNK + _both("knee"), _knee_lat),
    "ankle_lift": BodyFeatureDef(_TRUNK + _both("ankle"), _ankle_lift),
    "head_pitch": BodyFeatureDef(_TRUNK + ("nose", "left_ear", "right_ear"), _head_pitch),
    "head_yaw": BodyFeatureDef(_TRUNK + ("nose", "left_ear", "right_ear"), _head_yaw),
    "elbow_angle_both": BodyFeatureDef(_both("shoulder", "elbow", "wrist"), _elbow_angle_both),
    "shoulder_angle_both": BodyFeatureDef(_both("hip", "shoulder", "elbow"), _shoulder_angle_both),
    "hand_ratio": BodyFeatureDef(_both("shoulder", "wrist"), _hand_ratio),
    "hand_out": BodyFeatureDef(_both("shoulder", "wrist"), _hand_out),
}
ALL_FEATURES = {**FEATURES, **BODY_FEATURES}


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

    for name, bdef in BODY_FEATURES.items():
        ids = [KEYPOINT_INDEX[k] for k in bdef.keypoints]
        if any(pose.conf[i] < min_conf for i in ids):
            continue
        value = bdef.compute({k: pose.xy[KEYPOINT_INDEX[k]] for k in bdef.keypoints})
        if value is not None:
            values[name] = float(value)
    return FrameFeatures(side=best_side, values=values)
