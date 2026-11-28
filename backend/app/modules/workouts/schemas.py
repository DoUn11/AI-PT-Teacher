from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.workouts.body_parts import PARTS

from app.modules.workouts.models import Exercise


class SessionCreate(BaseModel):
    exercise: Exercise


class RepIn(BaseModel):
    index: int = Field(ge=1, description="세션 안에서 1부터 시작하는 반복 번호")
    score: int = Field(ge=0, le=100)
    min_angle: float = Field(ge=0, le=180, description="반복 중 가장 깊었을 때의 주 관절 각도")
    feedback: list[str] = Field(default_factory=list, max_length=10)
    parts: dict[str, float] | None = Field(
        default=None, description="부위별 약점 {arms, torso, legs} 0~1. 생략하면 피드백 코드에서 만든다"
    )
    completed_at: datetime | None = None

    @field_validator("parts")
    @classmethod
    def _check_parts(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return None
        if set(v) - set(PARTS):
            raise ValueError(f"parts keys must be in {PARTS}")
        if any(not 0 <= x <= 1 for x in v.values()):
            raise ValueError("parts values must be between 0 and 1")
        return {p: round(float(v.get(p, 0.0)), 3) for p in PARTS}

    @field_validator("completed_at")
    @classmethod
    def _require_timezone(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("completed_at must include a timezone offset (e.g. 2026-11-05T10:00:00Z)")
        return v


class RepsIn(BaseModel):
    reps: list[RepIn] = Field(min_length=1, max_length=200)


class RepsAccepted(BaseModel):
    accepted: int
    duplicates: int


class RepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    score: int
    min_angle: float
    feedback: list[str]
    parts: dict[str, float] = Field(validation_alias="resolved_parts")
    completed_at: datetime


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exercise: Exercise
    status: str
    started_at: datetime
    finished_at: datetime | None
    total_reps: int
    avg_score: float | None


class SessionDetail(SessionOut):
    reps: list[RepOut]


class ExerciseStats(BaseModel):
    exercise: Exercise
    sessions: int
    total_reps: int
    avg_score: float | None


# ---- 리포트 / 인사이트 -------------------------------------------------------

PartName = Literal["arms", "torso", "legs"]
Level = Literal["good", "notice", "weak"]


class PartReport(BaseModel):
    part: PartName
    weakness: float  # 0(문제 없음) ~ 1(매번 크게 벗어남)
    level: Level
    flagged_reps: int  # 이 부위가 지적된 반복 수
    top_code: str | None  # 이 부위에서 가장 많이 지적된 피드백 코드


class IssueOut(BaseModel):
    code: str
    count: int
    rate: float  # 전체 반복 중 이 지적이 나온 비율
    parts: list[PartName]


class Focus(BaseModel):
    part: PartName
    code: str | None


class PreviousSession(BaseModel):
    id: int
    started_at: datetime
    avg_score: float


class ReportOut(BaseModel):
    """끝난 세션 한 번을 풀어 주는 리포트 (웹 '세션 리포트' 화면)."""

    session: SessionDetail
    duration_seconds: float | None
    avg_score: float | None
    best_rep: int | None
    worst_rep: int | None
    perfect_reps: int
    parts: list[PartReport]
    weakest_part: PartName | None  # 문제가 없으면 None
    focus: Focus | None  # 다음에 신경 쓸 부위와 지적 사항
    issues: list[IssueOut]  # 많이 지적된 순
    previous: PreviousSession | None  # 같은 운동의 바로 이전 세션
    score_delta: float | None  # 이전 세션 대비 평균 점수 차


class TotalsOut(BaseModel):
    sessions: int
    reps: int
    avg_score: float | None
    active_days: int


class DayOut(BaseModel):
    date: str
    sessions: int
    reps: int
    avg_score: float | None


class PartTrend(BaseModel):
    part: PartName
    weakness: float
    level: Level
    trend: float | None  # 최근 절반 - 이전 절반. 음수면 좋아지는 중


class Recommendation(BaseModel):
    kind: Literal["fix", "variety", "start"]
    exercise: Exercise
    part: PartName | None
    code: str | None


class RecentSession(BaseModel):
    id: int
    exercise: Exercise
    started_at: datetime
    total_reps: int
    avg_score: float | None
    parts: dict[str, float]


class InsightsOut(BaseModel):
    """홈과 기록 화면이 쓰는 요약: 연속 운동일, 주간 활동, 부위별 약점과 추세, 다음 운동 추천."""

    totals: TotalsOut
    streak_days: int
    today_done: bool
    week: list[DayOut]
    parts: list[PartTrend]
    weakest_part: PartName | None
    recommendation: Recommendation | None
    recent: list[RecentSession]  # 오래된 순 (추이 그래프용)
