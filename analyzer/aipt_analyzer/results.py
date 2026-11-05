from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aipt_analyzer.pose import Pose


@dataclass(frozen=True)
class RepSummary:
    """한 번의 반복 동안 관측한 특징 요약. 임계값/모델 학습의 한 행(row)이 된다."""

    index: int
    start_t: float
    end_t: float
    primary_feature: str
    primary_min: float
    stats: dict[str, dict[str, float]]  # 특징 이름 -> {"min", "max", "mean"}

    @property
    def duration(self) -> float:
        return self.end_t - self.start_t

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start_t": round(self.start_t, 3),
            "end_t": round(self.end_t, 3),
            "duration": round(self.duration, 3),
            "primary_feature": self.primary_feature,
            "primary_min": round(self.primary_min, 2),
            "stats": {
                name: {k: round(v, 2) for k, v in s.items()} for name, s in self.stats.items()
            },
        }


@dataclass(frozen=True)
class Judgement:
    score: int  # 0~100
    feedback: tuple[str, ...] = ()  # 피드백 코드 (백엔드로 그대로 업로드됨)


@dataclass(frozen=True)
class RepResult:
    summary: RepSummary
    score: int
    feedback: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary.to_dict(), "score": self.score, "feedback": list(self.feedback)}


@dataclass
class FrameResult:
    visible: bool  # 주 특징을 계산할 만큼 몸이 보이는가
    pose: Pose | None
    features: dict[str, float] = field(default_factory=dict)  # 스무딩된 현재 값
    phase: str = "waiting"  # waiting | up | moving | down
    reps: int = 0
    live_feedback: list[str] = field(default_factory=list)
    rep: RepResult | None = None  # 이 프레임에서 한 번의 반복이 끝났으면 채워진다
