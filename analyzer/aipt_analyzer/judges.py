"""반복 판정기. 교체 가능한 인터페이스 + 임계값 기반 기본 구현.

나중에 학습한 모델을 쓰려면 RepJudge를 구현하고 register_judge로 등록한 뒤,
운동 설정 JSON의 "judge": {"type": "<등록한 이름>", ...}만 바꾸면 된다.
"""
from __future__ import annotations

from typing import Callable, Mapping, Protocol

from aipt_analyzer.config import ExerciseConfig, FormCheck
from aipt_analyzer.results import Judgement, RepSummary


class RepJudge(Protocol):
    def judge(self, summary: RepSummary) -> Judgement:
        """끝난 반복 하나를 채점한다."""
        ...

    def live_feedback(self, values: Mapping[str, float]) -> list[str]:
        """진행 중인 프레임의 즉시 피드백 코드 (없으면 빈 목록)."""
        ...


JudgeFactory = Callable[[ExerciseConfig], RepJudge]
_JUDGES: dict[str, JudgeFactory] = {}


def register_judge(name: str) -> Callable[[JudgeFactory], JudgeFactory]:
    def deco(factory: JudgeFactory) -> JudgeFactory:
        _JUDGES[name] = factory
        return factory

    return deco


def build_judge(cfg: ExerciseConfig) -> RepJudge:
    kind = cfg.judge.get("type", "threshold")
    try:
        factory = _JUDGES[kind]
    except KeyError:
        raise ValueError(f"unknown judge type {kind!r}; registered: {sorted(_JUDGES)}") from None
    return factory(cfg)


@register_judge("threshold")
class ThresholdJudge:
    """설정의 깊이 규칙 + 자세 검사로 점수를 매기는 규칙 기반 판정기."""

    def __init__(self, cfg: ExerciseConfig) -> None:
        self._cfg = cfg

    def judge(self, summary: RepSummary) -> Judgement:
        cfg = self._cfg
        depth = cfg.depth
        feedback: list[str] = []

        shortfall = max(0.0, summary.primary_min - depth.target)
        depth_penalty = min(shortfall * depth.penalty_per_degree, 100.0 - depth.min_score)
        if summary.primary_min > depth.target + depth.tolerance:
            feedback.append(depth.code)

        form_penalty = 0.0
        for check in cfg.form_checks:
            stat = summary.stats.get(check.feature)
            if not check.enabled or stat is None:  # 꺼 둔 조건, 이 반복에서 관절이 안 보인 조건은 판단하지 않는다
                continue
            value = stat[check.stat]
            if self._violates(check, value):
                feedback.append(check.code)
                form_penalty += self._penalty(check, value)

        raw = max(0.0, min(100.0, 100.0 - depth_penalty - form_penalty))
        step = cfg.score_step
        score = int(round(raw / step) * step)
        if score >= 100:  # 감점이 반올림으로 0이 될 만큼 기준선 근처면 지적하지 않는다 (100점 = 지적 없음)
            feedback = []
        return Judgement(score=score, feedback=tuple(feedback))

    def live_feedback(self, values: Mapping[str, float]) -> list[str]:
        codes = []
        for check in self._cfg.form_checks:
            if not (check.enabled and check.live):
                continue
            value = values.get(check.feature)
            if value is not None and self._violates(check, value):
                codes.append(check.code)
        return codes

    @staticmethod
    def _violates(check: FormCheck, value: float) -> bool:
        return value > check.limit if check.violates_above else value < check.limit

    @staticmethod
    def _penalty(check: FormCheck, value: float) -> float:
        """위반한 조건의 감점. full_at이 있으면 기준에서 멀어질수록 penalty까지 늘어난다."""
        if check.full_at is None:
            return check.penalty
        frac = (value - check.limit) / (check.full_at - check.limit)
        return check.penalty * min(1.0, max(0.0, frac))
