import json
from types import SimpleNamespace

import httpx
import pytest

from aipt_analyzer import Analyzer, load_exercise_config
from aipt_analyzer.control import Command
from aipt_analyzer.results import RepResult, RepSummary
from aipt_analyzer.severity import rep_parts
from aipt_analyzer.station import Station
from aipt_analyzer.uploader import BackendClient
from tests.helpers import cycle, run, squat_pose


def summary(primary_min, stats):
    return RepSummary(1, 0.0, 1.0, "knee_angle", primary_min, stats)


# ---- 부위별 약점 -----------------------------------------------------------


def test_rep_parts_is_all_zero_when_nothing_was_flagged():
    cfg = load_exercise_config("squat")
    s = summary(95.0, {"trunk_lean": {"min": 0, "max": 60, "mean": 30}})
    assert rep_parts(cfg, s, ()) == {"arms": 0.0, "torso": 0.0, "legs": 0.0}


def test_rep_parts_blames_the_body_part_of_each_flagged_code():
    cfg = load_exercise_config("squat")
    lean = next(c for c in cfg.form_checks if c.code == "trunk_lean")
    bad = lean.limit + 25
    parts = rep_parts(cfg, summary(95.0, {"trunk_lean": {"min": 0, "max": bad, "mean": bad}}), ["trunk_lean"])
    assert parts["torso"] > 0.8 and parts["arms"] == 0 and parts["legs"] == 0  # 많이 벗어나면 1에 가깝다
    shallow = rep_parts(cfg, summary(cfg.depth.target + 40, {}), [cfg.depth.code])
    assert shallow["legs"] > 0.8 and shallow["torso"] == 0  # 깊이(주 특징 knee_angle)는 다리


def test_rep_parts_just_over_the_limit_is_a_mild_weakness_not_zero():
    cfg = load_exercise_config("squat")
    lean = next(c for c in cfg.form_checks if c.code == "trunk_lean")
    parts = rep_parts(cfg, summary(95.0, {"trunk_lean": {"min": 0, "max": lean.limit + 0.01, "mean": 0}}), ["trunk_lean"])
    assert 0.4 <= parts["torso"] < 0.5


def test_a_part_flagged_by_two_checks_keeps_the_larger_weakness():
    cfg = load_exercise_config("sidecrunch")  # elbow_knee_close: 팔+다리
    code = "elbow_knee_close"
    check = next(c for c in cfg.form_checks if c.code == code)
    value = check.limit + (check.full_at - check.limit)
    s = RepSummary(1, 0, 1, cfg.primary_feature, 0.0, {check.feature: {"min": value, "max": value, "mean": value}})
    parts = rep_parts(cfg, s, [code])
    assert parts["arms"] > 0.9 and parts["legs"] > 0.9 and parts["torso"] == 0


# ---- 업로드에 parts가 실린다 ------------------------------------------------


class Backend:
    def __init__(self):
        self.requests = []
        self.next_id = 0

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/auth/login"):
            return httpx.Response(200, json={"access_token": "t"})
        if path.endswith("/workouts/sessions"):
            self.next_id += 1
            return httpx.Response(201, json={"id": self.next_id})
        if path.endswith("/reps"):
            return httpx.Response(201, json={"accepted": 1, "duplicates": 0})
        if path.endswith("/finish"):
            sid = int(path.split("/")[-2])
            return httpx.Response(200, json={"id": sid, "total_reps": 1, "avg_score": 80.0})
        return httpx.Response(404)

    def bodies(self, suffix):
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith(suffix)]


def make_client(backend):
    c = BackendClient("http://api.test", "a@b.c", "pw", client=httpx.Client(transport=httpx.MockTransport(backend)))
    c.login()
    return c


def test_upload_includes_parts_only_when_session_started_with_a_config():
    cfg = load_exercise_config("squat")
    backend = Backend()
    c = make_client(backend)
    c.start_session("squat", cfg)
    s = summary(95.0, {"trunk_lean": {"min": 0, "max": 80, "mean": 40}})
    c.enqueue(RepResult(s, 70, ("trunk_lean",)))
    assert c.flush()
    rep = backend.bodies("/reps")[0]["reps"][0]
    assert rep["parts"]["torso"] > 0.8 and set(rep["parts"]) == {"arms", "torso", "legs"}

    c.start_session("squat")  # cfg 없이: 예전 형식 그대로
    c.enqueue(RepResult(s, 70, ("trunk_lean",)))
    assert c.flush()
    assert "parts" not in backend.bodies("/reps")[1]["reps"][0]


def test_finish_keeps_the_finished_session_for_the_web():
    backend = Backend()
    c = make_client(backend)
    c.start_session("squat")
    assert c.finish() and c.last_finished == {"id": 1, "total_reps": 1, "avg_score": 80.0}
    c.start_session("squat")
    assert c.last_finished is None  # 새 세션을 열면 이전 요약은 비운다


# ---- 제어 명령 ------------------------------------------------------------


def test_control_parses_start_stop_and_select():
    pytest.importorskip("paho.mqtt.client")
    from aipt_analyzer.control import ExerciseControl

    ctl = ExerciseControl("mqtt://127.0.0.1:1/x/control")
    msg = lambda b: SimpleNamespace(payload=b, retain=False)
    try:
        for payload, want in [
            (b'{"action":"start","exercise":"kneeup"}', Command("start", "kneeup")),
            (b'{"action":"start"}', Command("start", None)),
            (b'{"action":"stop"}', Command("stop", None)),
            (b'{"exercise":"pushup"}', Command("select", "pushup")),
        ]:
            ctl._on_message(None, None, msg(payload))
            assert ctl.take() == want
        for bad in (b'{"action":"jump"}', b'{"action":"start","exercise":"nope"}', b'{"action":"select"}', b"[]", b"x"):
            ctl._on_message(None, None, msg(bad))
            assert ctl.take() is None
    finally:
        ctl.close()


# ---- 세션 생명주기 ---------------------------------------------------------


class Info:
    def __init__(self):
        self.calls = []

    def set_config(self, cfg):
        self.calls.append(("config", cfg.exercise))

    def set_session(self, state, session_id, started_at, last):
        self.calls.append(("session", state, session_id, last))


def make_station(**kw):
    backend = Backend()
    info = Info()
    station = Station(load_exercise_config("squat"), client=make_client(backend), info=info, autostart=False, **kw)
    return station, backend, info


def test_station_starts_idle_and_only_counts_inside_a_session():
    station, backend, info = make_station()
    assert station.state == "idle" and station.parts.worker is None and info.calls[-1][:3] == ("session", "idle", None)
    assert station.handle(Command("stop")) is None  # 준비 중에 종료는 무시
    assert backend.requests[-1].url.path.endswith("/auth/login")  # 세션을 열지 않았다

    parts = station.handle(Command("start"))
    assert station.state == "active" and station.session_id == 1 and parts.worker is not None
    assert info.calls[-1][:3] == ("session", "active", 1)
    assert station.handle(Command("start")) is None  # 같은 운동으로 이미 진행 중


def test_station_stop_flushes_reps_finishes_and_reports_the_session():
    station, backend, info = make_station()
    parts = station.handle(Command("start"))
    for r in run(parts.analyzer, [squat_pose(k, lean=70) for k in cycle(172, 90)]):
        if r.rep:
            parts.worker.submit(r.rep)
    idle = station.handle(Command("stop"))
    assert station.state == "idle" and idle.worker is None and idle.analyzer.counter.reps == 0  # 새 분석기
    assert backend.bodies("/reps")[0]["reps"][0]["parts"]["torso"] > 0
    assert any(r.url.path.endswith("/sessions/1/finish") for r in backend.requests)
    assert station.last == {"id": 1, "exercise": "squat", "total_reps": 1, "avg_score": 80.0, "saved": True}
    assert info.calls[-1] == ("session", "idle", None, station.last)


def test_station_switching_exercise_while_active_closes_and_reopens():
    station, backend, info = make_station()
    station.handle(Command("start"))
    parts = station.handle(Command("select", "kneeup"))
    assert station.state == "active" and station.cfg.exercise == "kneeup" and station.session_id == 2
    assert any(r.url.path.endswith("/sessions/1/finish") for r in backend.requests)
    assert parts.analyzer.cfg.exercise == "kneeup"


def test_station_select_while_idle_just_swaps_the_exercise():
    station, backend, info = make_station()
    parts = station.handle(Command("select", "kneeup"))
    assert station.state == "idle" and parts.worker is None and station.cfg.exercise == "kneeup"
    assert ("config", "kneeup") in info.calls and backend.next_id == 0
    assert station.handle(Command("select", "kneeup")) is None


def test_station_start_with_another_exercise_and_unknown_exercise():
    station, backend, info = make_station()
    station.handle(Command("start", "sidecrunch"))
    assert station.state == "active" and station.cfg.exercise == "sidecrunch"
    assert station.handle(Command("select", "nope")) is None and station.cfg.exercise == "sidecrunch"


def test_station_survives_a_backend_that_is_down():
    def down(request):
        raise httpx.ConnectError("down")

    client = BackendClient("http://x", "a", "b", client=httpx.Client(transport=httpx.MockTransport(down)))
    station = Station(load_exercise_config("squat"), client=client, autostart=False)
    parts = station.handle(Command("start"))
    assert station.state == "active" and station.session_id is None and parts.worker is None  # 화면 분석은 계속
    station.handle(Command("stop"))
    assert station.last["saved"] is False and station.state == "idle"


def test_station_autostart_opens_a_session_immediately():
    backend = Backend()
    station = Station(load_exercise_config("squat"), client=make_client(backend), autostart=True)
    assert station.state == "active" and station.session_id == 1
    station.shutdown()
    assert station.state == "idle" and any(r.url.path.endswith("/finish") for r in backend.requests)


def test_publisher_hides_counts_while_idle_and_reports_the_session():
    pytest.importorskip("paho.mqtt.client")
    from aipt_analyzer.publisher import AnalysisPublisher
    from datetime import datetime, timezone

    cfg = load_exercise_config("squat")
    pub = AnalysisPublisher("mqtt://127.0.0.1:1/x/analysis", cfg, rate_hz=1000)
    try:
        results = run(Analyzer(cfg), [squat_pose(k, lean=20) for k in cycle(172, 90)])
        pub.set_session("idle", None, None, None)
        p = pub.payload(results[-1], now=10.0)
        assert p["reps"] == 0 and p["last_rep"] is None and p["session"]["state"] == "idle"
        pub.set_session("active", 5, datetime(2026, 1, 1, tzinfo=timezone.utc), None)
        p = pub.payload(next(r for r in results if r.rep), now=11.0)  # 반복이 끝난 프레임
        assert p["reps"] == 1 and p["last_rep"]["index"] == 1
        assert p["session"] == {"state": "active", "id": 5, "started_at": "2026-01-01T00:00:00+00:00", "last": None}
        json.dumps(p)
    finally:
        pub.close()
