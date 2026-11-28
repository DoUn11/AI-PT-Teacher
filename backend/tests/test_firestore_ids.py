import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db
from tests.conftest import register_and_login

API = "/api/v1/workouts"


def _memory_engine():
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


def _use_fresh_local_db(monkeypatch):
    """다른 PC처럼 번호가 1부터 다시 시작하는 로컬 DB로 바꾼다."""
    engine = _memory_engine()
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))


def _finish_one_session(client, headers):
    sid = client.post(f"{API}/sessions", json={"exercise": "pushup"}, headers=headers).json()["id"]
    rep = {"index": 1, "score": 90, "min_angle": 80.0, "feedback": []}
    client.post(f"{API}/sessions/{sid}/reps", json={"reps": [rep]}, headers=headers)
    assert client.post(f"{API}/sessions/{sid}/finish", headers=headers).status_code == 200


def _ids(docs, collection):
    return [p.split("/")[1] for p in docs if p.startswith(f"{collection}/") and p.count("/") == 1]


def test_user_document_is_named_by_uuid(client, firestore_docs):
    register_and_login(client)
    [doc_id] = _ids(firestore_docs, "users")
    assert str(uuid.UUID(doc_id)) == doc_id


def test_session_document_is_named_by_uuid_and_points_at_user(client, firestore_docs):
    _finish_one_session(client, register_and_login(client))
    [user_id] = _ids(firestore_docs, "users")
    [session_id] = _ids(firestore_docs, "workout_sessions")
    assert str(uuid.UUID(session_id)) == session_id
    assert firestore_docs[f"workout_sessions/{session_id}"]["user_id"] == user_id
    assert f"workout_sessions/{session_id}/reps/1" in firestore_docs


def test_two_local_databases_do_not_overwrite_each_other(client, firestore_docs, monkeypatch):
    _finish_one_session(client, register_and_login(client, email="a@example.com"))
    _use_fresh_local_db(monkeypatch)
    _finish_one_session(client, register_and_login(client, email="b@example.com"))
    assert len(_ids(firestore_docs, "users")) == 2
    assert len(_ids(firestore_docs, "workout_sessions")) == 2


def test_existing_local_database_gets_stable_uuids():
    engine = _memory_engine()
    with engine.begin() as conn:
        # uuid 컬럼이 없던 이전 스키마
        for table in ("users", "workout_sessions"):
            conn.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"))
            conn.execute(text(f"INSERT INTO {table} (id) VALUES (1), (2)"))

    def read():
        with engine.connect() as conn:
            return [
                conn.execute(text(f"SELECT uuid FROM {t} ORDER BY id")).scalars().all()
                for t in ("users", "workout_sessions")
            ]

    db.add_missing_uuids(engine)
    first = read()
    db.add_missing_uuids(engine)

    assert read() == first
    values = first[0] + first[1]
    assert len(set(values)) == 4
    assert all(str(uuid.UUID(v)) == v for v in values)
