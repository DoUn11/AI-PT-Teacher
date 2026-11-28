"""자세 조건(FormCheck)이 얼마나 어긋났는가(0~1)와 그 조건이 말하는 신체 부위.

오버레이의 관절선 색과 웹 패널의 조건 표시가 같은 기준을 쓰도록 한 곳에 둔다.
0 = 올바름(초록), 0.5 = 기준선 바로 위(노랑), 1 = 확실히 벗어남(빨강).
"""
from __future__ import annotations

from aipt_analyzer.config import ExerciseConfig, FormCheck

PARTS = ("arms", "torso", "legs")

# 어느 특징이 어느 부위의 자세를 말해 주는가
FEATURE_PARTS: dict[str, tuple[str, ...]] = {
    "knee_angle": ("legs",),
    "hip_angle": ("torso", "legs"),
    "elbow_angle": ("arms",),
    "body_line": ("torso", "legs"),
    "trunk_lean": ("torso",),
    "torso_sw": ("torso",),
    "knee_gap": ("legs",),
    "hip_flex": ("torso", "legs"),
    "knee_lat": ("legs",),
    "ankle_lift": ("legs",),
    "head_pitch": ("torso",),  # 얼굴 선은 그리지 않으므로 목·어깨가 있는 상체로 표시한다
    "head_yaw": ("torso",),
    "elbow_angle_both": ("arms",),
    "shoulder_angle_both": ("arms",),
    "hand_ratio": ("arms",),
}
# 같은 특징이라도 조건이 다른 부위를 말하는 경우 (특징보다 우선)
CODE_PARTS: dict[str, tuple[str, ...]] = {
    "elbow_knee_close": ("arms", "legs"),
}

_MIN_MARGIN = 0.01


def check_parts(check: FormCheck) -> tuple[str, ...]:
    return CODE_PARTS.get(check.code) or FEATURE_PARTS.get(check.feature, ())


def code_parts(code: str, cfg: ExerciseConfig) -> tuple[str, ...]:
    """피드백 코드가 가리키는 부위. 자세 조건 -> 깊이 규칙(주 특징) 순으로 찾는다."""
    for check in cfg.form_checks:
        if check.code == code:
            return check_parts(check)
    if code == cfg.depth.code:
        return FEATURE_PARTS.get(cfg.primary_feature, ())
    return ()


def check_severity(check: FormCheck, value: float) -> float:
    """값이 기준(limit)에 다가갈수록 0에서 올라 기준에서 0.5, full_at(없으면 기준+15%)에서 1이 된다.

    위반 방향(violates_above)이 above면 값이 클수록, below면 작을수록 나쁘다.
    기준의 15%를 여유 폭으로 쓰므로 각도(수십)와 비율(1 안팎) 특징을 같은 식으로 다룬다.
    """
    sign = 1.0 if check.violates_above else -1.0
    x, limit = sign * value, sign * check.limit
    margin = max(abs(check.limit) * 0.15, _MIN_MARGIN)
    if x <= limit:
        return 0.5 * min(1.0, max(0.0, (x - (limit - margin)) / margin))
    end = sign * check.full_at if check.full_at is not None else limit + margin
    span = end - limit if end > limit else margin
    return 0.5 + 0.5 * min(1.0, (x - limit) / span)


def _weakness(overshoot: float) -> float:
    """지적된 항목의 약점 정도. 기준을 막 넘으면 0.4, 확실히 벗어나면 1."""
    return 0.4 + 0.6 * min(1.0, max(0.0, overshoot))


def rep_parts(cfg: ExerciseConfig, summary, feedback) -> dict[str, float]:
    """끝난 반복에서 부위별 약점(0~1). 판정기가 지적한 코드(feedback)만 센다 - 지적이 없으면 모두 0.

    웹 리포트가 '어느 부위가 약한가'를 그릴 때 쓴다. 같은 부위를 여러 조건이 말하면 가장 큰 값.
    """
    out = {p: 0.0 for p in PARTS}
    flagged = set(feedback)
    if not flagged:
        return out
    for check in cfg.form_checks:
        if not check.enabled or check.code not in flagged:
            continue
        stat = summary.stats.get(check.feature)
        if stat is None:
            continue
        w = _weakness((check_severity(check, stat[check.stat]) - 0.5) * 2)
        for part in check_parts(check):
            out[part] = max(out[part], w)
    if cfg.depth.code in flagged:
        d = cfg.depth
        margin = max(abs(d.target) * 0.15, _MIN_MARGIN)
        w = _weakness((summary.primary_min - (d.target + d.tolerance)) / margin)
        for part in FEATURE_PARTS.get(cfg.primary_feature, ()):
            out[part] = max(out[part], w)
    return {p: round(v, 3) for p, v in out.items()}
