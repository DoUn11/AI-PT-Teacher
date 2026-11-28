from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.workouts.body_parts import PARTS

from app.modules.workouts.models import Exercise


class SessionCreate(BaseModel):
    exercise: Exercise
    # 이번 운동의 목표 (셋 다 함께 보내거나 모두 생략 = 목표 없이 자유롭게)
    target_sets: int | None = Field(default=None, ge=1, le=10)
    target_reps: int | None = Field(default=None, ge=1, le=100)
    rest_seconds: int | None = Field(default=None, ge=0, le=600)

    @model_validator(mode="after")
    def _all_or_none(self):
        given = [v is not None for v in (self.target_sets, self.target_reps)]
        if any(given) and not all(given):
            raise ValueError("target_sets and target_reps must be given together")
        if self.target_sets is not None and self.rest_seconds is None:
            self.rest_seconds = 60
        return self


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
    target_sets: int | None = None
    target_reps: int | None = None
    rest_seconds: int | None = None


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


class SetResult(BaseModel):
    set: int
    reps: int
    avg_score: float | None


class PlanResult(BaseModel):
    """목표가 있는 세션의 결과: 목표 대비 얼마나 했나, 세트별 횟수와 점수."""

    sets: int
    reps: int
    rest_seconds: int | None
    completed_sets: int
    completion: float  # 0~1, 목표 총 횟수 대비
    per_set: list[SetResult]


class SetsReps(BaseModel):
    sets: int
    reps: int


class PlanChange(BaseModel):
    """한 세션을 평가한 결과. change: up(올림) | hold(그대로) | down(낮춤)"""

    session_id: int
    change: Literal["up", "hold", "down"]
    reason: str  # 웹이 문구로 바꾼다: ready_up new_set wait_more low_quality incomplete struggled at_floor at_max
    need_more: int | None = None
    before: SetsReps
    after: SetsReps
    applied: bool  # 이미 목표에 반영했는가 (자동 조정이 꺼져 있으면 False)


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
    plan: PlanResult | None = None  # 목표가 있던 세션만
    next_plan: PlanChange | None = None  # 이 세션을 평가해 정한 다음 목표


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


# ---- 운동 목표(플랜) ----------------------------------------------------------

Pace = Literal["slow", "normal", "fast"]


class PlanIn(BaseModel):
    sets: int = Field(ge=1, le=10)
    reps: int = Field(ge=1, le=100)
    rest_seconds: int = Field(ge=0, le=600)
    pace: Pace = "normal"
    auto_progress: bool = True


class PlanLimits(BaseModel):
    floor: int
    ceiling: int
    max_sets: int


class LastSession(BaseModel):
    id: int
    started_at: datetime
    reps_done: int
    target_total: int | None
    avg_score: float | None


class PlanOut(BaseModel):
    exercise: Exercise
    sets: int
    reps: int
    rest_seconds: int
    pace: Pace
    auto_progress: bool
    is_default: bool  # 아직 저장한 목표가 없어 기록으로 정한 시작값이다
    suggestion: PlanChange | None  # 자동 조정이 꺼져 있을 때 제안 (적용하려면 PUT)
    last_change: PlanChange | None
    limits: PlanLimits
    last_session: LastSession | None
