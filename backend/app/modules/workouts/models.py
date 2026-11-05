import enum
from datetime import datetime

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UtcDateTime
from app.modules.auth.models import utcnow


class Exercise(str, enum.Enum):
    SQUAT = "squat"
    PUSHUP = "pushup"


class SessionStatus(str, enum.Enum):
    ACTIVE = "active"
    FINISHED = "finished"


class WorkoutSession(Base):
    __tablename__ = "workout_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    exercise: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10), default=SessionStatus.ACTIVE.value)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    total_reps: Mapped[int] = mapped_column(Integer, default=0)
    avg_score: Mapped[float | None] = mapped_column(Float)

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
    completed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    session: Mapped[WorkoutSession] = relationship(back_populates="reps")
