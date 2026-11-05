"""반복 판정기. 교체 가능한 인터페이스 + 임계값 기반 기본 구현.

나중에 학습한 모델을 쓰려면 RepJudge를 구현하고 register_judge로 등록한 뒤,
운동 설정 JSON의 "judge": {"type": "<등록한 이름>", ...}만 바꾸면 된다.
"""
from __future__ import annotations

from typing import Callable, Mapping, Protocol

from aipt_analyzer.config import ExerciseConfig
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
            if stat is None:  # 이 반복에서 해당 관절이 안 보였으면 판단하지 않는다
                continue
            if self._violates(check.stat, check.limit, stat[check.stat]):
                feedback.append(check.code)
                form_penalty += check.penalty

        score = int(round(max(0.0, min(100.0, 100.0 - depth_penalty - form_penalty))))
        return Judgement(score=score, feedback=tuple(feedback))

    def live_feedback(self, values: Mapping[str, float]) -> list[str]:
        codes = []
        for check in self._cfg.form_checks:
            value = values.get(check.feature)
            if value is not None and self._violates(check.stat, check.limit, value):
                codes.append(check.code)
        return codes

    @staticmethod
    def _violates(stat: str, limit: float, value: float) -> bool:
        return value > limit if stat == "max" else value < limit
