import pytest
from sqlalchemy import create_engine, inspect, text

from app import db
from app.modules.camera import service
from app.modules.workouts import progression as pg
from app.modules.workouts.progression import Result, evaluate
from tests.conftest import register_and_login
from tests.test_camera import SESSION_ACTIVE, SESSION_IDLE, SessionControl, _info_bytes

API = "/api/v1/workouts"


def R(i, done=30, score=90, sets=3, reps=10):
    return Result(i, sets, reps, done, score)


# ---- 규칙 (순수 함수) -----------------------------------------------------


def test_a_session_is_clean_only_when_the_goal_is_met_with_good_form():
    assert R(1).ready and R(1, score=75).ready
    assert not R(1, score=74).ready  # 자세가 나쁘면 채웠어도 올리지 않는다
    assert not R(1, done=29).ready
    assert R(1, done=20).completion == pytest.approx(2 / 3) and R(1, done=20).completed_sets == 2
    assert R(1, done=99).completion == 1.0 and R(1, done=99).completed_sets == 3
    assert R(1, done=20).struggled and R(1, score=54).struggled and not R(1, done=21, score=60).struggled


def test_pace_decides_how_many_clean_sessions_it_takes_to_move_up():
    sessions = [R(1), R(2), R(3)]
    slow = [evaluate("pushup", 3, 10, "slow", sessions[:n]) for n in (1, 2, 3)]
    assert [d.change for d in slow] == ["hold", "hold", "up"]
    assert (slow[0].reason, slow[0].need_more, slow[1].need_more) == ("wait_more", 2, 1)
    assert (slow[2].sets, slow[2].reps) == (3, 11)
    normal = [evaluate("pushup", 3, 10, "normal", sessions[:n]) for n in (1, 2)]
    assert [d.change for d in normal] == ["hold", "up"] and normal[1].reps == 11
    fast = evaluate("pushup", 3, 10, "fast", sessions[:1])
    assert (fast.change, fast.reps, fast.reason) == ("up", 12, "ready_up")


def test_a_bad_session_resets_the_streak_and_explains_why_it_held():
    d = evaluate("pushup", 3, 10, "normal", [R(1), R(2, score=70), R(3)])  # 가운데가 자세 불량
    assert (d.change, d.need_more) == ("hold", 1)
    assert evaluate("pushup", 3, 10, "fast", [R(1, score=70)]).reason == "low_quality"
    assert evaluate("pushup", 3, 10, "fast", [R(1, done=25)]).reason == "incomplete"  # 83%: 힘들진 않았지만 다 못 채움
    assert evaluate("pushup", 3, 10, "fast", []) is None


def test_struggling_lowers_reps_but_not_below_the_floor():
    d = evaluate("pushup", 3, 10, "fast", [R(1), R(2, done=15)])
    assert (d.change, d.reps, d.reason) == ("down", 9, "struggled")
    assert evaluate("pushup", 3, 4, "fast", [R(1, reps=4, done=3)]).reason == "at_floor"
    assert evaluate("pushup", 3, 10, "normal", [R(1, score=50)]).change == "down"


def test_reaching_the_rep_ceiling_adds_a_set_and_resets_reps():
    d = evaluate("pushup", 3, 15, "fast", [R(1, reps=15, done=45)])  # 푸시업 상한 15
    assert (d.change, d.sets, d.reps, d.reason) == ("up", 4, 10, "new_set")  # 15의 70% -> 10
    d = evaluate("pushup", 3, 14, "fast", [R(1, reps=14, done=42)])  # +2는 상한을 넘으므로 세트를 늘린다
    assert (d.sets, d.reps) == (4, 10)
    d = evaluate("pushup", 5, 14, "fast", [R(1, sets=5, reps=14, done=70)])  # 세트가 최대: 횟수만 상한까지
    assert (d.sets, d.reps, d.reason) == (5, 15, "ready_up")
    d = evaluate("pushup", 5, 15, "fast", [R(1, sets=5, reps=15, done=75)])
    assert (d.change, d.reason) == ("hold", "at_max")


def test_baseline_uses_free_workout_history_or_exercise_defaults():
    assert pg.baseline("pushup", []) == (3, 8, 60)
    assert pg.baseline("kneeup", []) == (3, 12, 45)
    assert pg.baseline("pushup", [10, 12, 10]) == (3, 8, 60)  # 중앙값 10의 80%
    assert pg.baseline("pushup", [40]) == (3, 15, 60)  # 상한으로 자른다
    assert pg.baseline("pushup", [2]) == (3, 4, 60)  # 하한으로 올린다
    assert pg.baseline("unknown", []) == (3, 10, 60)


# ---- API --------------------------------------------------------------------


def _plan(client, h, exercise="pushup", **over):
    body = {"sets": 3, "reps": 10, "rest_seconds": 60, "pace": "fast", "auto_progress": True, **over}
    r = client.put(f"{API}/plans/{exercise}", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _run(client, h, exercise="pushup", reps=30, score=90, sets=3, per=10, finish=True):
    sid = client.post(f"{API}/sessions", json={"exercise": exercise, "target_sets": sets, "target_reps": per, "rest_seconds": 45}, headers=h).json()["id"]
    body = [{"index": i, "score": score, "min_angle": 100.0, "feedback": []} for i in range(1, reps + 1)]
    if body:
        assert client.post(f"{API}/sessions/{sid}/reps", json={"reps": body}, headers=h).status_code == 201
    if finish:
        assert client.post(f"{API}/sessions/{sid}/finish", headers=h).status_code == 200
    return sid


def test_plans_start_from_defaults_and_from_free_workout_history(client):
    h = register_and_login(client)
    plans = client.get(f"{API}/plans", headers=h).json()
    assert [p["exercise"] for p in plans] == ["pushup", "kneeup", "sidecrunch"]
    push = plans[0]
    assert (push["sets"], push["reps"], push["rest_seconds"], push["pace"], push["is_default"]) == (3, 8, 60, "normal", True)
    assert push["limits"] == {"floor": 4, "ceiling": 15, "max_sets": 5} and push["last_session"] is None and push["suggestion"] is None

    sid = client.post(f"{API}/sessions", json={"exercise": "pushup"}, headers=h).json()["id"]  # 목표 없이 자유롭게 12회
    client.post(f"{API}/sessions/{sid}/reps", json={"reps": [{"index": i, "score": 80, "min_angle": 90.0} for i in range(1, 13)]}, headers=h)
    client.post(f"{API}/sessions/{sid}/finish", headers=h)
    push = client.get(f"{API}/plans", headers=h).json()[0]
    assert push["reps"] == 10 and push["is_default"] is True  # 12의 80%
    assert push["last_session"]["reps_done"] == 12 and push["last_session"]["target_total"] is None


def test_saving_a_plan_validates_and_is_per_user(client):
    h = register_and_login(client)
    assert _plan(client, h, sets=4, reps=12)["is_default"] is False
    assert client.put(f"{API}/plans/pushup", json={"sets": 6, "reps": 10, "rest_seconds": 60, "pace": "normal", "auto_progress": True}, headers=h).status_code == 422  # 푸시업은 최대 5세트
    for bad in ({"reps": 0}, {"sets": 11}, {"rest_seconds": 601}, {"pace": "turbo"}):
        r = client.put(f"{API}/plans/pushup", json={"sets": 3, "reps": 10, "rest_seconds": 60, "pace": "normal", "auto_progress": True, **bad}, headers=h)
        assert r.status_code == 422
    assert client.put(f"{API}/plans/yoga", json={"sets": 3, "reps": 10, "rest_seconds": 60}, headers=h).status_code == 422
    assert client.put(f"{API}/plans/pushup", json={"sets": 3, "reps": 10, "rest_seconds": 60}).status_code == 401
    other = register_and_login(client, "b@example.com")
    assert client.get(f"{API}/plans", headers=other).json()[0]["is_default"] is True


def test_session_stores_its_goal_and_rejects_half_goals(client):
    h = register_and_login(client)
    sid = _run(client, h, reps=0, finish=False)
    s = client.get(f"{API}/sessions/{sid}", headers=h).json()
    assert (s["target_sets"], s["target_reps"], s["rest_seconds"]) == (3, 10, 45)
    free = client.post(f"{API}/sessions", json={"exercise": "pushup"}, headers=h).json()
    assert free["target_sets"] is None and free["rest_seconds"] is None
    assert client.post(f"{API}/sessions", json={"exercise": "pushup", "target_sets": 3}, headers=h).status_code == 422
    assert client.post(f"{API}/sessions", json={"exercise": "pushup", "target_sets": 3, "target_reps": 10}, headers=h).json()["rest_seconds"] == 60


def test_auto_progress_raises_the_plan_after_a_clean_session_and_the_report_says_so(client):
    h = register_and_login(client)
    _plan(client, h, pace="fast")
    sid = _run(client, h)
    plan = client.get(f"{API}/plans", headers=h).json()[0]
    assert (plan["sets"], plan["reps"]) == (3, 12)  # fast: +2회
    assert plan["last_change"] == {"session_id": sid, "change": "up", "reason": "ready_up", "need_more": None,
                                   "before": {"sets": 3, "reps": 10}, "after": {"sets": 3, "reps": 12}, "applied": True}
    assert plan["suggestion"] is None
    report = client.get(f"{API}/sessions/{sid}/report", headers=h).json()
    assert report["next_plan"]["after"] == {"sets": 3, "reps": 12}
    assert report["plan"] == {"sets": 3, "reps": 10, "rest_seconds": 45, "completed_sets": 3, "completion": 1.0,
                              "per_set": [{"set": k, "reps": 10, "avg_score": 90.0} for k in (1, 2, 3)]}

    sid2 = _run(client, h, per=12, reps=36)  # 새 목표(3x12)를 채움 -> 또 올림
    assert client.get(f"{API}/plans", headers=h).json()[0]["reps"] == 14
    # 평가는 마지막 것만 저장하므로 이전 세션의 리포트에는 다음 목표가 나오지 않는다
    assert client.get(f"{API}/sessions/{sid}/report", headers=h).json()["next_plan"] is None
    assert client.get(f"{API}/sessions/{sid2}/report", headers=h).json()["next_plan"]["session_id"] == sid2


def test_slow_pace_waits_for_repeated_clean_sessions_and_never_double_counts(client):
    h = register_and_login(client)
    _plan(client, h, pace="slow")
    _run(client, h)
    p = client.get(f"{API}/plans", headers=h).json()[0]
    assert (p["reps"], p["last_change"]["reason"], p["last_change"]["need_more"]) == (10, "wait_more", 2)
    assert p["suggestion"] is None  # hold는 제안이 아니다
    _run(client, h)
    assert client.get(f"{API}/plans", headers=h).json()[0]["reps"] == 10
    _run(client, h)  # 세 번째에 비로소 +1
    p = client.get(f"{API}/plans", headers=h).json()[0]
    assert p["reps"] == 11
    _run(client, h, per=11, reps=33)  # 올린 뒤에는 처음부터 다시 3번
    assert client.get(f"{API}/plans", headers=h).json()[0]["reps"] == 11


def test_poor_form_holds_the_target_even_when_every_rep_was_done(client):
    h = register_and_login(client)
    _plan(client, h, pace="fast")
    _run(client, h, score=60)  # 다 채웠지만 자세 60점
    p = client.get(f"{API}/plans", headers=h).json()[0]
    assert (p["reps"], p["last_change"]["change"], p["last_change"]["reason"]) == (10, "hold", "low_quality")


def test_struggling_lowers_the_target_and_manual_mode_only_suggests(client):
    h = register_and_login(client)
    _plan(client, h, pace="fast")
    _run(client, h, reps=12)  # 40%만 함
    p = client.get(f"{API}/plans", headers=h).json()[0]
    assert (p["reps"], p["last_change"]["change"]) == (9, "down")

    _plan(client, h, "kneeup", pace="fast", auto_progress=False, reps=12, rest_seconds=45)
    sid = _run(client, h, "kneeup", per=12, reps=36)
    p = client.get(f"{API}/plans", headers=h).json()[1]
    assert p["reps"] == 12  # 자동 조정이 꺼져 있으면 그대로
    assert p["suggestion"]["after"] == {"sets": 3, "reps": 14} and p["suggestion"]["applied"] is False
    assert client.get(f"{API}/sessions/{sid}/report", headers=h).json()["next_plan"]["applied"] is False
    applied = _plan(client, h, "kneeup", pace="fast", auto_progress=False, reps=14, rest_seconds=45)  # 제안을 적용 = 저장
    assert applied["reps"] == 14 and applied["suggestion"] is None and applied["last_change"] is None


def test_changing_the_target_by_hand_restarts_the_evaluation_but_resaving_the_same_one_does_not(client):
    h = register_and_login(client)
    _plan(client, h, pace="normal")
    _run(client, h)  # 잘 함 1번 (normal은 2번 필요)
    _plan(client, h, pace="normal")  # 같은 값으로 다시 저장 (시작 버튼이 하는 일): 기록은 유지
    _run(client, h)
    assert client.get(f"{API}/plans", headers=h).json()[0]["reps"] == 11
    _plan(client, h, pace="normal", reps=8)  # 사용자가 직접 바꿈: 그 전 기록은 새 목표와 무관
    _run(client, h, per=8, reps=24)
    assert client.get(f"{API}/plans", headers=h).json()[0]["reps"] == 8  # 아직 1번째


def test_a_session_without_a_saved_plan_creates_one_and_unfinished_or_empty_sessions_are_ignored(client):
    h = register_and_login(client)
    _run(client, h, reps=0)  # 목표만 있고 아무것도 안 함
    assert client.get(f"{API}/plans", headers=h).json()[0]["is_default"] is True
    _run(client, h)
    p = client.get(f"{API}/plans", headers=h).json()[0]
    assert p["is_default"] is False and p["pace"] == "normal"
    free = client.post(f"{API}/sessions", json={"exercise": "kneeup"}, headers=h).json()["id"]
    client.post(f"{API}/sessions/{free}/finish", headers=h)
    assert client.get(f"{API}/plans", headers=h).json()[1]["is_default"] is True  # 목표 없는 세션은 목표를 만들지 않는다
    assert client.get(f"{API}/sessions/{free}/report", headers=h).json()["plan"] is None


def test_report_splits_an_unfinished_goal_into_sets(client):
    h = register_and_login(client)
    sid = _run(client, h, reps=14, score=80)  # 3세트 목표 중 1세트 + 4회
    plan = client.get(f"{API}/sessions/{sid}/report", headers=h).json()["plan"]
    assert plan["completed_sets"] == 1 and plan["completion"] == pytest.approx(0.467)
    assert plan["per_set"] == [{"set": 1, "reps": 10, "avg_score": 80.0}, {"set": 2, "reps": 4, "avg_score": 80.0}]


def test_old_database_gets_the_goal_columns():
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE workout_sessions (id INTEGER PRIMARY KEY, exercise VARCHAR(20))"))
    db.upgrade_schema(engine)
    assert {"target_sets", "target_reps", "rest_seconds"} <= {c["name"] for c in inspect(engine).get_columns("workout_sessions")}


# ---- 웹 -> 분석기 제어 (목표 포함) ------------------------------------------------


def test_start_forwards_the_goal_and_skip_rest_needs_a_running_session(client, monkeypatch):
    h = register_and_login(client)
    ctl = SessionControl()
    monkeypatch.setattr(service, "control", ctl)
    info = lambda **o: service.info_hub.on_info(_info_bytes(**o))
    CAM = "/api/v1/camera"
    info(session=SESSION_IDLE)
    assert client.post(f"{CAM}/session/skip-rest", headers=h).status_code == 409
    r = client.post(f"{CAM}/session/start", json={"exercise": "pushup", "sets": 3, "reps": 10, "rest_seconds": 45}, headers=h)
    assert r.status_code == 202
    assert ctl.commands[-1] == {"action": "start", "exercise": "pushup", "plan": {"sets": 3, "reps": 10, "rest_seconds": 45}}
    client.post(f"{CAM}/session/start", json={"exercise": "pushup", "sets": 2, "reps": 5}, headers=h)
    assert ctl.commands[-1]["plan"] == {"sets": 2, "reps": 5, "rest_seconds": 60}
    client.post(f"{CAM}/session/start", json={"exercise": "pushup"}, headers=h)
    assert "plan" not in ctl.commands[-1]  # 목표 없이 자유롭게
    assert client.post(f"{CAM}/session/start", json={"exercise": "pushup", "sets": 3}, headers=h).status_code == 422
    assert client.post(f"{CAM}/session/start", json={"exercise": "pushup", "sets": 11, "reps": 5}, headers=h).status_code == 422

    info(session=SESSION_ACTIVE)
    assert client.post(f"{CAM}/session/skip-rest", headers=h).status_code == 202
    assert ctl.commands[-1] == {"action": "skip_rest"}


def test_analysis_exposes_set_progress(client):
    h = register_and_login(client)
    progress = {"sets": 3, "reps": 10, "rest_seconds": 45, "set_index": 2, "set_reps": 4, "reps_done": 14, "status": "working", "remaining": None}
    service.info_hub.on_info(_info_bytes(session={**SESSION_ACTIVE, "progress": progress}))
    info = client.get("/api/v1/camera/analysis", headers=h).json()["info"]
    assert info["session"]["progress"] == progress
    service.info_hub.on_info(_info_bytes(session={**SESSION_ACTIVE, "progress": {**progress, "status": "dancing"}}))  # 모르는 상태는 통째로 무시
    assert client.get("/api/v1/camera/analysis", headers=h).json()["info"]["session"]["progress"] == progress


def test_cors_preflight_allows_saving_a_plan_from_the_web_origin(client):
    # 브라우저는 PUT 전에 OPTIONS로 허용 여부를 묻는다. 허용하지 않으면 목표를 저장할 수 없다.
    r = client.options(f"{API}/plans/pushup", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code == 200 and "PUT" in r.headers["access-control-allow-methods"]
