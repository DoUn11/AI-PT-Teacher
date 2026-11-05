from tests.conftest import register_and_login

API = "/api/v1/workouts"


def _rep(i, score=80, angle=95.0, feedback=None):
    return {"index": i, "score": score, "min_angle": angle, "feedback": feedback or []}


def _new_session(client, headers, exercise="squat"):
    r = client.post(f"{API}/sessions", json={"exercise": exercise}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_session_lifecycle(client):
    h = register_and_login(client)
    sid = _new_session(client, h)

    r = client.post(
        f"{API}/sessions/{sid}/reps",
        json={"reps": [_rep(1, 90), _rep(2, 70, 120.0, ["shallow_depth"])]},
        headers=h,
    )
    assert r.status_code == 201
    assert r.json() == {"accepted": 2, "duplicates": 0}

    r = client.post(f"{API}/sessions/{sid}/finish", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "finished"
    assert body["total_reps"] == 2
    assert body["avg_score"] == 80.0
    assert [rep["index"] for rep in body["reps"]] == [1, 2]
    assert body["reps"][1]["feedback"] == ["shallow_depth"]


def test_rep_upload_is_idempotent(client):
    h = register_and_login(client)
    sid = _new_session(client, h)
    payload = {"reps": [_rep(1), _rep(2)]}
    client.post(f"{API}/sessions/{sid}/reps", json=payload, headers=h)
    r = client.post(
        f"{API}/sessions/{sid}/reps", json={"reps": [_rep(2), _rep(3)]}, headers=h
    )
    assert r.json() == {"accepted": 1, "duplicates": 1}
    detail = client.get(f"{API}/sessions/{sid}", headers=h).json()
    assert [rep["index"] for rep in detail["reps"]] == [1, 2, 3]


def test_cannot_add_reps_after_finish_but_finish_is_repeatable(client):
    h = register_and_login(client)
    sid = _new_session(client, h)
    client.post(f"{API}/sessions/{sid}/reps", json={"reps": [_rep(1)]}, headers=h)
    first = client.post(f"{API}/sessions/{sid}/finish", headers=h).json()
    again = client.post(f"{API}/sessions/{sid}/finish", headers=h)
    assert again.status_code == 200
    assert again.json()["finished_at"] == first["finished_at"]
    r = client.post(f"{API}/sessions/{sid}/reps", json={"reps": [_rep(2)]}, headers=h)
    assert r.status_code == 409


def test_finish_empty_session(client):
    h = register_and_login(client)
    sid = _new_session(client, h)
    body = client.post(f"{API}/sessions/{sid}/finish", headers=h).json()
    assert body["total_reps"] == 0
    assert body["avg_score"] is None


def test_users_cannot_access_each_others_sessions(client):
    alice = register_and_login(client, email="alice@example.com")
    bob = register_and_login(client, email="bob@example.com")
    sid = _new_session(client, alice)

    assert client.get(f"{API}/sessions/{sid}", headers=bob).status_code == 404
    assert client.post(f"{API}/sessions/{sid}/finish", headers=bob).status_code == 404
    r = client.post(f"{API}/sessions/{sid}/reps", json={"reps": [_rep(1)]}, headers=bob)
    assert r.status_code == 404
    assert client.get(f"{API}/sessions", headers=bob).json() == []


def test_list_filter_and_pagination(client):
    h = register_and_login(client)
    ids = [_new_session(client, h, "squat"), _new_session(client, h, "pushup"), _new_session(client, h, "squat")]

    all_ = client.get(f"{API}/sessions", headers=h).json()
    assert [s["id"] for s in all_] == ids[::-1]  # 최신순

    squats = client.get(f"{API}/sessions?exercise=squat", headers=h).json()
    assert {s["exercise"] for s in squats} == {"squat"}

    page = client.get(f"{API}/sessions?limit=1&offset=1", headers=h).json()
    assert [s["id"] for s in page] == [ids[1]]
    assert client.get(f"{API}/sessions?limit=0", headers=h).status_code == 422


def test_invalid_rep_payloads_rejected(client):
    h = register_and_login(client)
    sid = _new_session(client, h)
    bad = [
        {"reps": []},
        {"reps": [_rep(0)]},  # index는 1부터
        {"reps": [_rep(1, score=101)]},
        {"reps": [_rep(1, angle=200.0)]},
    ]
    for payload in bad:
        r = client.post(f"{API}/sessions/{sid}/reps", json=payload, headers=h)
        assert r.status_code == 422, payload
    assert client.post(f"{API}/sessions", json={"exercise": "burpee"}, headers=h).status_code == 422


def test_timestamps_are_utc_aware_and_naive_input_rejected(client):
    h = register_and_login(client)
    sid = _new_session(client, h)

    naive = {**_rep(1), "completed_at": "2026-11-05T10:00:00"}
    r = client.post(f"{API}/sessions/{sid}/reps", json={"reps": [naive]}, headers=h)
    assert r.status_code == 422

    kst = {**_rep(1), "completed_at": "2026-11-05T19:00:00+09:00"}
    r = client.post(f"{API}/sessions/{sid}/reps", json={"reps": [kst]}, headers=h)
    assert r.status_code == 201
    detail = client.get(f"{API}/sessions/{sid}", headers=h).json()
    assert detail["reps"][0]["completed_at"].startswith("2026-11-05T10:00:00")
    assert detail["started_at"].endswith("Z") or detail["started_at"].endswith("+00:00")


def test_stats_only_count_finished_sessions(client):
    h = register_and_login(client)
    done = _new_session(client, h, "squat")
    client.post(f"{API}/sessions/{done}/reps", json={"reps": [_rep(1, 100), _rep(2, 50)]}, headers=h)
    client.post(f"{API}/sessions/{done}/finish", headers=h)
    active = _new_session(client, h, "pushup")
    client.post(f"{API}/sessions/{active}/reps", json={"reps": [_rep(1)]}, headers=h)
    empty = _new_session(client, h, "squat")
    client.post(f"{API}/sessions/{empty}/finish", headers=h)

    stats = client.get(f"{API}/stats", headers=h).json()
    assert stats == [{"exercise": "squat", "sessions": 2, "total_reps": 2, "avg_score": 75.0}]
