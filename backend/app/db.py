import uuid
from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import DateTime, Engine, create_engine, inspect, text
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


def new_uuid() -> str:
    """PC마다 따로 매겨지는 정수 ID 대신 Firestore 문서 이름으로 쓰는 값."""
    return str(uuid.uuid4())


def add_missing_uuids(engine: Engine) -> None:
    """uuid 컬럼이 없던 기존 DB에 컬럼을 추가하고 빈 값을 채운다 (여러 번 실행해도 안전).

    create_all은 이미 있는 테이블을 바꾸지 않아서 필요하다. Alembic 도입 시 대체한다.
    """
    with engine.begin() as conn:
        for table in ("users", "workout_sessions"):
            if "uuid" not in {column["name"] for column in inspect(conn).get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN uuid VARCHAR(36)"))
                conn.execute(text(f"CREATE UNIQUE INDEX ix_{table}_uuid ON {table} (uuid)"))
            missing = conn.execute(text(f"SELECT id FROM {table} WHERE uuid IS NULL")).scalars()
            for row_id in missing.all():
                conn.execute(
                    text(f"UPDATE {table} SET uuid = :uuid WHERE id = :id"),
                    {"uuid": new_uuid(), "id": row_id},
                )


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


# create_all은 이미 있는 테이블에 새 칸을 더하지 못한다. Alembic을 도입하기 전까지 가볍게 채운다.
_ADDED_COLUMNS = {
    "rep_records": {"parts": "JSON"},
    "workout_sessions": {"target_sets": "INTEGER", "target_reps": "INTEGER", "rest_seconds": "INTEGER"},
}


def upgrade_schema(bind) -> None:
    insp = inspect(bind)
    for table, columns in _ADDED_COLUMNS.items():
        if not insp.has_table(table):
            continue
        have = {c["name"] for c in insp.get_columns(table)}
        with bind.begin() as conn:
            for name, ddl in columns.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
