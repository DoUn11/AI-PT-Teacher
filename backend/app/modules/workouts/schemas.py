from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.workouts.models import Exercise


class SessionCreate(BaseModel):
    exercise: Exercise


class RepIn(BaseModel):
    index: int = Field(ge=1, description="세션 안에서 1부터 시작하는 반복 번호")
    score: int = Field(ge=0, le=100)
    min_angle: float = Field(ge=0, le=180, description="반복 중 가장 깊었을 때의 주 관절 각도")
    feedback: list[str] = Field(default_factory=list, max_length=10)
    completed_at: datetime | None = None

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
