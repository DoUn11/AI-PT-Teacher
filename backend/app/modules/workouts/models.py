import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UtcDateTime, new_uuid
from app.modules.auth.models import utcnow


class Exercise(str, enum.Enum):
    SQUAT = "squat"
    PUSHUP = "pushup"
    KNEEUP = "kneeup"
    SIDECRUNCH = "sidecrunch"


class SessionStatus(str, enum.Enum):
    ACTIVE = "active"
    FINISHED = "finished"


class WorkoutSession(Base):
    __tablename__ = "workout_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=new_uuid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    exercise: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10), default=SessionStatus.ACTIVE.value)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    total_reps: Mapped[int] = mapped_column(Integer, default=0)
    avg_score: Mapped[float | None] = mapped_column(Float)
    # 이번 운동의 목표. 목표 없이 자유롭게 한 세션은 비어 있다
    target_sets: Mapped[int | None] = mapped_column(Integer)
    target_reps: Mapped[int | None] = mapped_column(Integer)
    rest_seconds: Mapped[int | None] = mapped_column(Integer)

    reps: Mapped[list["RepRecord"]] = relationship(
        back_populates="session",
        order_by="RepRecord.index",
        cascade="all, delete-orphan",
    )


class RepRecord(Base):
    __tablename__ = "rep_records"
    __table_args__ = (UniqueConstraint("session_id", "index", name="uq_rep_session_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("workout_sessions.id", ondelete="CASCADE"), index=True
    )
    index: Mapped[int] = mapped_column(Integer)
    score: Mapped[int] = mapped_column(Integer)
    min_angle: Mapped[float] = mapped_column(Float)
    feedback: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 부위별 약점 {arms, torso, legs} 0~1. 분석기가 보낸 값 (없으면 피드백 코드에서 만든다)
    parts: Mapped[dict[str, float] | None] = mapped_column(JSON, default=None)
    completed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    session: Mapped[WorkoutSession] = relationship(back_populates="reps")

    @property
    def resolved_parts(self) -> dict[str, float]:
        from app.modules.workouts.body_parts import resolve_parts

        return resolve_parts(self.session.exercise, self.feedback, self.parts)


class ExercisePlan(Base):
    """사용자별 운동 목표(세트 x 횟수)와 강도를 올리는 방식. 운동 하나에 하나."""

    __tablename__ = "exercise_plans"
    __table_args__ = (UniqueConstraint("user_id", "exercise", name="uq_plan_user_exercise"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    exercise: Mapped[str] = mapped_column(String(20))
    sets: Mapped[int] = mapped_column(Integer)
    reps: Mapped[int] = mapped_column(Integer)  # 세트당 횟수
    rest_seconds: Mapped[int] = mapped_column(Integer, default=60)
    pace: Mapped[str] = mapped_column(String(10), default="normal")  # slow | normal | fast
    auto_progress: Mapped[bool] = mapped_column(Boolean, default=True)
    # 이 세션까지는 목표 조정에 반영했다 (같은 세션으로 두 번 올리지 않게)
    pointer_session_id: Mapped[int | None] = mapped_column(Integer)
    suggestion: Mapped[dict | None] = mapped_column(JSON)  # 자동 조정이 꺼져 있을 때 제안 (사용자가 적용)
    last_change: Mapped[dict | None] = mapped_column(JSON)  # 가장 최근 평가 결과 (리포트가 보여 준다)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)
