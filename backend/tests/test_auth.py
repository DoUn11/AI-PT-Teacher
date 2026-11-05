from tests.conftest import register_and_login


def test_register_login_me(client):
    headers = register_and_login(client, email="User@Example.com")
    r = client.get("/api/v1/users/me", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "user@example.com"  # 소문자로 정규화
    assert "password" not in body and "password_hash" not in body


def test_duplicate_email_conflict(client):
    register_and_login(client, email="dup@example.com")
    r = client.post(
        "/api/v1/auth/register",
        json={"email": "DUP@example.com", "password": "password123", "nickname": "x"},
    )
    assert r.status_code == 409


def test_wrong_password_and_unknown_user_look_the_same(client):
    register_and_login(client, email="a@example.com")
    wrong = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "nope-nope"})
    unknown = client.post("/api/v1/auth/login", json={"email": "z@example.com", "password": "nope-nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_weak_or_oversized_password_rejected(client):
    for pw in ("short", "가" * 30):  # 8자 미만 / 72바이트 초과
        r = client.post(
            "/api/v1/auth/register",
            json={"email": "p@example.com", "password": pw, "nickname": "p"},
        )
        assert r.status_code == 422


def test_protected_routes_require_valid_token(client):
    assert client.get("/api/v1/users/me").status_code == 401
    bad = {"Authorization": "Bearer not-a-token"}
    assert client.get("/api/v1/users/me", headers=bad).status_code == 401
    assert client.get("/api/v1/workouts/sessions", headers=bad).status_code == 401
