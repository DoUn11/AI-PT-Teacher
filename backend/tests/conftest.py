import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db, models  # noqa: F401


@pytest.fixture()
def client(monkeypatch):
    # 테스트마다 격리된 인메모리 DB를 쓴다. get_db는 db.SessionLocal을 호출 시점에 읽는다.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(
        db, "SessionLocal", sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    )
    db.Base.metadata.create_all(engine)

    from app.main import app

    with TestClient(app) as c:
        yield c


def register_and_login(client, email="a@example.com", password="password123", nickname="a"):
    r = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "nickname": nickname},
    )
    assert r.status_code == 201, r.text
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
