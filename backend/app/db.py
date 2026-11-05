from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.core.config import settings


class UtcDateTime(TypeDecorator):
    """항상 timezone-aware UTC로 저장·반환한다.

    SQLite는 tzinfo를 버려 naive datetime을 돌려주므로, DB 종류와 관계없이
    API 응답의 시각 형식이 일정하도록 로드 시 UTC를 붙인다.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed; use timezone-aware UTC")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    kwargs = {}
    if url.startswith("sqlite"):
        # FastAPI는 요청을 스레드풀에서 처리하므로 SQLite 스레드 제한을 풀어준다.
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, pool_pre_ping=True, **kwargs)


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    # SessionLocal은 호출 시점에 모듈 전역에서 읽는다 (테스트에서 교체 가능).
    with SessionLocal() as db:
        yield db
