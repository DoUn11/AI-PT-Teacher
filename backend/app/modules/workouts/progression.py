"""세트·횟수 목표를 내 기록에 맞춰 조금씩 올리는 규칙 (점진적 과부하). DB를 모르는 순수 함수.

원칙: **자세가 좋을 때만** 늘린다. 목표를 다 채우고(완료율 100%) 평균 점수가 기준(75점) 이상인 세션만 '잘 한' 세션이다.
얼마나 빨리 올릴지는 사용자가 고른다 (pace).

    slow    잘 한 세션이 3번 이어지면 +1회
    normal  잘 한 세션이 2번 이어지면 +1회
    fast    잘 한 세션이 1번이면 +2회

횟수가 운동별 상한에 닿으면 세트를 하나 늘리고 횟수를 상한의 70%로 되돌린다 (이중 점진). 세트는 운동별 최대까지.
목표의 70%도 못 채웠거나 평균 점수가 55점 미만이면 횟수를 1회 낮춘다 (무리하지 않게).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

QUALITY_OK = 75
QUALITY_STRUGGLE = 55
COMPLETION_STRUGGLE = 0.7
PACES = ("slow", "normal", "fast")


@dataclass(frozen=True)
class Pace:
    need: int  # 잘 한 세션이 이만큼 연속이어야 올린다
    step: int  # 올릴 때 늘리는 횟수


PACE = {"slow": Pace(3, 1), "normal": Pace(2, 1), "fast": Pace(1, 2)}


@dataclass(frozen=True)
class Limits:
    floor: int  # 한 세트 최소 횟수
    ceiling: int  # 한 세트 최대 횟수. 넘으면 세트를 늘린다
    max_sets: int
    default_sets: int
    default_reps: int
    default_rest: int


LIMITS = {
    "pushup": Limits(4, 15, 5, 3, 8, 60),
    "kneeup": Limits(6, 20, 5, 3, 12, 45),
    "sidecrunch": Limits(6, 20, 5, 3, 12, 45),
    "squat": Limits(6, 20, 5, 3, 10, 60),
}
_FALLBACK = Limits(4, 20, 5, 3, 10, 60)


def limits(exercise: str) -> Limits:
    return LIMITS.get(exercise, _FALLBACK)


@dataclass(frozen=True)
class Result:
    """끝난 세션 하나: 목표(세트 x 횟수)와 실제로 한 횟수, 평균 점수."""

    id: int
    target_sets: int
    target_reps: int
    reps_done: int
    avg_score: float | None

    @property
    def completion(self) -> float:
        return min(1.0, self.reps_done / (self.target_sets * self.target_reps))

    @property
    def completed_sets(self) -> int:
        return min(self.target_sets, self.reps_done // self.target_reps)

    @property
    def ready(self) -> bool:
        return self.completion >= 1.0 and (self.avg_score or 0) >= QUALITY_OK

    @property
    def struggled(self) -> bool:
        return self.completion < COMPLETION_STRUGGLE or (self.avg_score is not None and self.avg_score < QUALITY_STRUGGLE)


@dataclass(frozen=True)
class Decision:
    change: str  # up | hold | down
    sets: int
    reps: int
    reason: str  # ready_up | new_set | wait_more | low_quality | incomplete | struggled | at_floor | at_max
    need_more: int | None = None  # wait_more일 때: 잘 한 세션이 몇 번 더 필요한가


def evaluate(exercise: str, sets: int, reps: int, pace: str, pending: Sequence[Result]) -> Decision | None:
    """아직 반영하지 않은 세션들(오래된 순)을 보고 다음 목표를 정한다. 세션이 없으면 None."""
    if not pending:
        return None
    lim, rule = limits(exercise), PACE[pace]
    last = pending[-1]

    if last.struggled:
        if reps > lim.floor:
            return Decision("down", sets, reps - 1, "struggled")
        return Decision("hold", sets, reps, "at_floor")

    streak = 0
    for r in reversed(pending):
        if not r.ready:
            break
        streak += 1
    if streak >= rule.need:
        if reps + rule.step <= lim.ceiling:
            return Decision("up", sets, reps + rule.step, "ready_up")
        if sets < lim.max_sets:
            return Decision("up", sets + 1, max(lim.floor, round(lim.ceiling * 0.7)), "new_set")
        if reps < lim.ceiling:
            return Decision("up", sets, lim.ceiling, "ready_up")
        return Decision("hold", sets, reps, "at_max")
    if last.ready:
        return Decision("hold", sets, reps, "wait_more", need_more=rule.need - streak)
    if last.completion >= 1.0:
        return Decision("hold", sets, reps, "low_quality")
    return Decision("hold", sets, reps, "incomplete")


def baseline(exercise: str, free_totals: Sequence[int]) -> tuple[int, int, int]:
    """목표 없이 한 기록(세션당 총 횟수)이 있으면 그 중앙값의 80%를 한 세트 횟수로, 없으면 운동별 기본값. (세트, 횟수, 휴식)"""
    lim = limits(exercise)
    if not free_totals:
        return lim.default_sets, lim.default_reps, lim.default_rest
    reps = round(median(free_totals) * 0.8)
    return lim.default_sets, max(lim.floor, min(lim.ceiling, reps)), lim.default_rest
