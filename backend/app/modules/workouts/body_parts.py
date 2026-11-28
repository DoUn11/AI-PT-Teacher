"""반복마다 어느 신체 부위가 약했는지. 웹 리포트가 몸 그림(팔·몸통·다리)에 색을 칠할 때 쓴다.

분석기(Pi/PC)가 반복마다 부위별 약점(parts, 0~1)을 계산해 올린다. 웹캠 분석처럼 parts를 안 보내는
쪽이나 예전 기록은 피드백 코드가 가리키는 부위를 DERIVED_WEAKNESS로 칠해 같은 화면에서 볼 수 있게 한다.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

PARTS = ("arms", "torso", "legs")

# 분석기의 severity.py(FEATURE_PARTS/CODE_PARTS)와 같은 대응. 운동 설정 JSON이 바뀌면 함께 고친다.
CODE_PARTS: dict[str, dict[str, tuple[str, ...]]] = {
    "pushup": {
        "shallow_depth": ("arms",),
        "hand_width": ("arms",),
        "hand_position": ("arms",),
        "body_line": ("torso", "legs"),
    },
    "kneeup": {
        "knee_low": ("legs",),
        "spine_bend": ("torso",),
        "gaze_front": ("torso",),
        "leg_tension": ("legs",),
    },
    "sidecrunch": {
        "knee_low": ("legs",),
        "hands_behind_head": ("arms",),
        "knee_side": ("legs",),
        "elbow_knee_close": ("arms", "legs"),
        "head_down": ("torso",),
        "gaze_front": ("torso",),
    },
    "squat": {"shallow_depth": ("legs",), "trunk_lean": ("torso",)},
}
DERIVED_WEAKNESS = 0.7  # 정도를 모를 때 지적된 부위에 칠하는 값

# 평균 약점(0~1)을 사람이 읽는 등급으로
LEVEL_NOTICE = 0.15
LEVEL_WEAK = 0.4


def code_parts(exercise: str, code: str) -> tuple[str, ...]:
    return CODE_PARTS.get(exercise, {}).get(code, ())


def derive_parts(exercise: str, feedback: Iterable[str]) -> dict[str, float]:
    out = {p: 0.0 for p in PARTS}
    for code in feedback:
        for part in code_parts(exercise, code):
            out[part] = DERIVED_WEAKNESS
    return out


def resolve_parts(exercise: str, feedback: Iterable[str], stored: Mapping[str, float] | None) -> dict[str, float]:
    """저장된 값이 있으면 그것을, 없으면 피드백 코드에서 만든다."""
    if stored:
        return {p: float(stored.get(p, 0.0)) for p in PARTS}
    return derive_parts(exercise, feedback)


def level(weakness: float) -> str:
    return "weak" if weakness >= LEVEL_WEAK else "notice" if weakness >= LEVEL_NOTICE else "good"
