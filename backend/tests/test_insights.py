from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text

from app import db
from app.modules.workouts import insights
from app.modules.workouts.body_parts import derive_parts, level, resolve_parts
from app.modules.workouts.insights import RepView, SessionView
from tests.conftest import register_and_login

API = "/api/v1/workouts"
NONE = {"arms": 0.0, "torso": 0.0, "legs": 0.0}


def rep(i, score=80, feedback=(), **parts):
    return RepView(i, score, tuple(feedback), {**NONE, **parts})


def view(id, exercise, day, reps, hour=10):
    return SessionView(id, exercise, datetime(2026, 11, day, hour, tzinfo=timezone.utc), tuple(reps))


# ---- 부위 대응 ------------------------------------------------------------


def test_feedback_codes_map_to_body_parts_per_exercise():
    assert derive_parts("pushup", ["shallow_depth"])["arms"] == 0.7
    assert derive_parts("kneeup", ["knee_low"]) == {**NONE, "legs": 0.7}  # 같은 코드도 운동마다 다를 수 있다
    both = derive_parts("sidecrunch", ["elbow_knee_close"])
    assert both["arms"] == both["legs"] == 0.7 and both["torso"] == 0
    assert derive_parts("pushup", ["unknown_code"]) == NONE and derive_parts("yoga", ["x"]) == NONE


def test_stored_parts_win_over_codes_and_levels():
    assert resolve_parts("pushup", ["shallow_depth"], {"arms": 0.4}) == {"arms": 0.4, "torso": 0.0, "legs": 0.0}
    assert resolve_parts("pushup", ["shallow_depth"], None)["arms"] == 0.7
    assert [level(x) for x in (0, 0.14, 0.15, 0.39, 0.4, 1)] == ["good", "good", "notice", "notice", "weak", "weak"]


# ---- 업로드/조회 ----------------------------------------------------------


def _session(client, h, exercise, reps):
    sid = client.post(f"{API}/sessions", json={"exercise": exercise}, headers=h).json()["id"]
    r = client.post(f"{API}/sessions/{sid}/reps", json={"reps": reps}, headers=h)
    assert r.status_code == 201, r.text
    assert client.post(f"{API}/sessions/{sid}/finish", headers=h).status_code == 200
    return sid


def _r(i, score, feedback=(), parts=None, completed_at=None):
    body = {"index": i, "score": score, "min_angle": 100.0, "feedback": list(feedback)}
    if parts is not None:
        body["parts"] = parts
    return body


def test_parts_are_stored_validated_and_derived_when_missing(client):
    h = register_and_login(client)
    sid = _session(client, h, "pushup", [
        _r(1, 70, ["shallow_depth"], {"arms": 0.9}),
        _r(2, 80, ["shallow_depth"]),  # 웹캠 분석처럼 parts를 안 보낸 경우
        _r(3, 100),
    ])
    reps = client.get(f"{API}/sessions/{sid}", headers=h).json()["reps"]
    assert reps[0]["parts"] == {"arms": 0.9, "torso": 0.0, "legs": 0.0}  # 저장된 값
    assert reps[1]["parts"] == {"arms": 0.7, "torso": 0.0, "legs": 0.0}  # 코드에서 만든 값
    assert reps[2]["parts"] == NONE

    sid2 = client.post(f"{API}/sessions", json={"exercise": "pushup"}, headers=h).json()["id"]
    for bad in ({"arms": 1.5}, {"arms": -0.1}, {"head": 0.5}):
        r = client.post(f"{API}/sessions/{sid2}/reps", json={"reps": [_r(1, 50, parts=bad)]}, headers=h)
        assert r.status_code == 422


def test_old_database_gets_the_parts_column():
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE rep_records (id INTEGER PRIMARY KEY, session_id INTEGER, feedback JSON)"))
    db.upgrade_schema(engine)
    assert "parts" in {c["name"] for c in inspect(engine).get_columns("rep_records")}
    db.upgrade_schema(engine)  # 두 번 불러도 안전


# ---- 세션 리포트 ----------------------------------------------------------


def test_report_ranks_weak_parts_and_issues(client):
    h = register_and_login(client)
    sid = _session(client, h, "sidecrunch", [
        _r(1, 60, ["elbow_knee_close", "head_down"], {"arms": 0.8, "legs": 0.8, "torso": 0.5}),
        _r(2, 70, ["elbow_knee_close"], {"arms": 0.6, "legs": 0.6}),
        _r(3, 100),
        _r(4, 90, ["head_down"], {"torso": 0.4}),
    ])
    rep = client.get(f"{API}/sessions/{sid}/report", headers=h).json()
    parts = {p["part"]: p for p in rep["parts"]}
    assert parts["arms"]["weakness"] == pytest.approx(0.35) and parts["arms"]["level"] == "notice"
    assert parts["legs"]["flagged_reps"] == 2 and parts["legs"]["top_code"] == "elbow_knee_close"
    assert parts["torso"]["top_code"] == "head_down" and parts["torso"]["weakness"] == pytest.approx(0.225)
    assert rep["weakest_part"] == "arms" and rep["focus"] == {"part": "arms", "code": "elbow_knee_close"}
    assert [(i["code"], i["count"], i["rate"]) for i in rep["issues"]] == [("elbow_knee_close", 2, 0.5), ("head_down", 2, 0.5)]
    assert rep["issues"][0]["parts"] == ["arms", "legs"]
    assert (rep["avg_score"], rep["best_rep"], rep["worst_rep"], rep["perfect_reps"]) == (80.0, 3, 1, 1)
    assert rep["previous"] is None and rep["score_delta"] is None
    assert rep["session"]["id"] == sid and rep["duration_seconds"] >= 0


def test_report_compares_with_the_previous_session_of_the_same_exercise(client):
    h = register_and_login(client)
    _session(client, h, "pushup", [_r(1, 60), _r(2, 60)])
    _session(client, h, "kneeup", [_r(1, 10)])  # 다른 운동은 비교하지 않는다
    sid = _session(client, h, "pushup", [_r(1, 80), _r(2, 90)])
    rep = client.get(f"{API}/sessions/{sid}/report", headers=h).json()
    assert rep["previous"]["avg_score"] == 60.0 and rep["score_delta"] == 25.0
    assert rep["weakest_part"] is None and rep["focus"] is None  # 지적이 없으면 약한 부위도 없다


def test_report_of_a_session_without_reps_and_of_someone_elses_session(client):
    h = register_and_login(client)
    sid = client.post(f"{API}/sessions", json={"exercise": "pushup"}, headers=h).json()["id"]
    client.post(f"{API}/sessions/{sid}/finish", headers=h)
    rep = client.get(f"{API}/sessions/{sid}/report", headers=h).json()
    assert rep["avg_score"] is None and rep["issues"] == [] and rep["weakest_part"] is None
    other = register_and_login(client, "b@example.com")
    assert client.get(f"{API}/sessions/{sid}/report", headers=other).status_code == 404  # 남의 세션


# ---- 인사이트 (순수 함수) ---------------------------------------------------


def test_streak_counts_back_from_today_or_yesterday():
    d = date(2026, 11, 29)
    days = {d - timedelta(days=n) for n in (0, 1, 2, 4)}
    assert insights.streak(days, d) == 3
    assert insights.streak(days - {d}, d) == 2  # 오늘 아직 안 했어도 어제까지의 연속은 살아 있다
    assert insights.streak({d - timedelta(days=2)}, d) == 0 and insights.streak(set(), d) == 0


def test_local_day_uses_the_users_timezone():
    t = datetime(2026, 11, 28, 16, 30, tzinfo=timezone.utc)  # 한국은 이미 29일 새벽
    assert insights.local_day(t, 540) == date(2026, 11, 29) and insights.local_day(t, 0) == date(2026, 11, 28)


def test_part_trends_flag_improvement_and_need_enough_sessions():
    sessions = [view(i, "pushup", i, [rep(1, 70, ["shallow_depth"], arms=w)]) for i, w in enumerate([0.8, 0.8, 0.2, 0.2], 1)]
    arms = insights.part_trends(sessions)[0]
    assert arms["part"] == "arms" and arms["weakness"] == pytest.approx(0.5) and arms["trend"] == pytest.approx(-0.6)
    assert insights.part_trends(sessions[:3])[0]["trend"] is None
    assert insights.part_trends([])[0] == {"part": "arms", "weakness": 0.0, "level": "good", "trend": None}


def test_recommendation_targets_the_weakest_part_in_the_exercise_where_it_is_weakest():
    today = date(2026, 11, 29)
    sessions = [
        view(1, "pushup", 20, [rep(1, 80, ["hand_width"], arms=0.5)]),
        view(2, "sidecrunch", 22, [rep(1, 60, ["elbow_knee_close"], arms=0.9, legs=0.9)]),
        view(3, "kneeup", 24, [rep(1, 90, ["knee_low"], legs=0.2)]),
    ]
    parts = insights.part_trends(sessions)
    assert insights.weakest(parts) == "arms"
    assert insights.recommend(sessions, parts, today, 0) == {
        "kind": "fix", "exercise": "sidecrunch", "part": "arms", "code": "elbow_knee_close",
    }
    assert insights.recommend([], insights.part_trends([]), today, 0)["kind"] == "start"
    clean = [view(1, "pushup", 20, [rep(1, 100)]), view(2, "kneeup", 25, [rep(1, 100)])]
    rec = insights.recommend(clean, insights.part_trends(clean), today, 0)
    assert rec["kind"] == "variety" and rec["exercise"] == "sidecrunch"  # 한 번도 안 해 본 운동부터


# ---- 인사이트 (API) ---------------------------------------------------------


def test_insights_for_a_new_user_is_empty_but_well_formed(client):
    h = register_and_login(client)
    body = client.get(f"{API}/insights?tz_offset=540", headers=h).json()
    assert body["totals"] == {"sessions": 0, "reps": 0, "avg_score": None, "active_days": 0}
    assert body["streak_days"] == 0 and body["today_done"] is False and len(body["week"]) == 7
    assert body["recommendation"]["kind"] == "start" and body["recent"] == [] and body["weakest_part"] is None
    assert client.get(f"{API}/insights", headers={}).status_code == 401
    assert client.get(f"{API}/insights?tz_offset=9999", headers=h).status_code == 422


def test_insights_summarizes_finished_sessions_only(client):
    h = register_and_login(client)
    _session(client, h, "pushup", [_r(1, 60, ["shallow_depth"], {"arms": 0.8}), _r(2, 80)])
    _session(client, h, "pushup", [_r(1, 90)])
    client.post(f"{API}/sessions", json={"exercise": "kneeup"}, headers=h)  # 진행 중인 세션은 세지 않는다
    body = client.get(f"{API}/insights", headers=h).json()
    assert body["totals"] == {"sessions": 2, "reps": 3, "avg_score": 76.7, "active_days": 1}
    assert body["today_done"] is True and body["streak_days"] == 1
    assert body["week"][-1]["sessions"] == 2 and body["week"][-1]["reps"] == 3 and body["week"][0]["sessions"] == 0
    assert [r["total_reps"] for r in body["recent"]] == [2, 1] and body["recent"][0]["exercise"] == "pushup"
    assert body["recent"][0]["parts"]["arms"] == pytest.approx(0.4)
    assert body["weakest_part"] == "arms" and body["recommendation"]["exercise"] == "pushup"
    assert body["recommendation"]["code"] == "shallow_depth"
