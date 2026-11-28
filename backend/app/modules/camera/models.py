from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UtcDateTime
from app.modules.auth.models import utcnow


class CameraEvent(Base):
    """Pi 카메라/브로커 연결 상태가 바뀐 시점 기록 (프레임 자체는 저장하지 않는다)."""

    __tablename__ = "camera_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    # stream_online | stream_offline | broker_connected | broker_disconnected
    event: Mapped[str] = mapped_column(String(30), index=True)
    detail: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)
