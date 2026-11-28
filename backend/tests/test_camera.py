import asyncio

import pytest

from app import db
from app.modules.camera import service
from app.modules.camera.models import CameraEvent
from app.modules.camera.router import mjpeg_parts
from tests.conftest import register_and_login

API = "/api/v1/camera"
JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes\xff\xd9"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture()
def hub(monkeypatch):
    """요청마다 깨끗한 hub. 시계를 직접 움직일 수 있다."""
    clock = Clock()
    h = service.CameraHub(offline_after=5.0, clock=clock)
    h.clock = clock
    h.events = []
    h.on_event = lambda event, detail: h.events.append(event)
    monkeypatch.setattr(service, "hub", h)
    # 분석 화면 hub도 격리한다 (이벤트 이름만 다르다)
    a = service.CameraHub(clock=clock, event_names=("analysis_online", "analysis_offline"))
    a.on_event = lambda event, detail: h.events.append(event)
    h.analyzed = a
    monkeypatch.setattr(service, "analyzed_hub", a)
    h.info = service.InfoHub(clock=clock)
    monkeypatch.setattr(service, "info_hub", h.info)
    return h


# ---- hub (상태 판단) ------------------------------------------------------


def test_frames_make_stream_online_and_silence_makes_it_offline(hub):
    assert hub.status()["online"] is False and hub.status()["last_frame_age_seconds"] is None

    hub.on_frame(JPEG)
    hub.on_frame(JPEG)
    assert hub.status()["online"] is True
    assert hub.events == ["stream_online"]  # 두 번째 프레임은 전환이 아니다

    hub.clock.t += 4
    hub.tick()
    assert hub.status()["online"] is True  # 아직 임계값 안

    hub.clock.t += 2
    hub.tick()
    assert hub.status()["online"] is False
    assert hub.events == ["stream_online", "stream_offline"]

    hub.on_frame(JPEG)  # 다시 오면 온라인 복귀
    assert hub.events[-1] == "stream_online"


def test_fps_counts_only_recent_frames(hub):
    for _ in range(10):
        hub.on_frame(JPEG)
        hub.clock.t += 0.5  # 2fps로 5초
    assert hub.status()["fps"] == pytest.approx(2.0, abs=0.3)
    hub.clock.t += 60
    assert hub.status()["fps"] == 0.0
    assert hub.status()["frames_total"] == 10


def test_non_jpeg_payload_is_ignored(hub):
    hub.on_frame(b"hello")
    assert hub.latest() == (None, 0) and hub.events == []


def test_broker_connection_changes_are_reported_once(hub):
    hub.set_broker_connected(True)
    hub.set_broker_connected(True)
    hub.set_broker_connected(False, "lost")
    assert hub.events == ["broker_connected", "broker_disconnected"]


def test_event_callback_failure_does_not_break_reception(hub):
    def boom(event, detail):
        raise RuntimeError("db down")

    hub.on_event = boom
    hub.on_frame(JPEG)  # 예외가 밖으로 나오면 안 된다
    assert hub.status()["online"] is True


# ---- API ------------------------------------------------------------------


def test_camera_endpoints_require_login(client, hub):
    for path in ("/status", "/snapshot", "/stream", "/events"):
        assert client.get(API + path).status_code == 401, path


def test_status_and_snapshot(client, hub):
    h = register_and_login(client)
    assert client.get(f"{API}/snapshot", headers=h).status_code == 404  # 아직 프레임 없음

    hub.on_frame(JPEG)
    r = client.get(f"{API}/status", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["online"] is True and body["frames_total"] == 1 and body["broker_connected"] is False

    r = client.get(f"{API}/snapshot", headers=h)
    assert r.status_code == 200 and r.content == JPEG
    assert r.headers["content-type"] == "image/jpeg"


def test_stream_accepts_query_token_but_other_routes_do_not(client, hub):
    h = register_and_login(client)
    token = h["Authorization"].split()[1]
    assert client.get(f"{API}/stream?token=bad").status_code == 401
    assert client.get(f"{API}/status?token={token}").status_code == 401  # 쿼리 토큰은 스트림 전용


def test_events_are_stored_newest_first(client, hub):
    h = register_and_login(client)
    for name in ("stream_online", "stream_offline"):
        with db.SessionLocal() as s:
            s.add(CameraEvent(event=name))
            s.commit()
    r = client.get(f"{API}/events?limit=1", headers=h)
    assert [e["event"] for e in r.json()] == ["stream_offline"]
    assert client.get(f"{API}/events?limit=0", headers=h).status_code == 422


# ---- MJPEG 조각 -----------------------------------------------------------


def test_mjpeg_parts_emits_each_new_frame_once_and_stops_on_disconnect(hub):
    async def run():
        calls = {"n": 0}

        async def is_disconnected():
            calls["n"] += 1
            if calls["n"] == 3:  # 두 번째 반복 전에 새 프레임 도착
                hub.on_frame(JPEG)
            return calls["n"] > 5

        hub.on_frame(JPEG)
        return [p async for p in mjpeg_parts(hub, is_disconnected, interval=0)]

    parts = asyncio.run(run())
    assert len(parts) == 2  # 프레임 2장, 같은 프레임을 반복해서 보내지 않는다
    assert parts[0].startswith(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ")
    assert JPEG in parts[0] and parts[0].endswith(b"\r\n")


# ---- 분석 화면 (analyzer가 오버레이를 그려 내보낸 프레임) ---------------------------

ANALYZED = b"\xff\xd8\xff\xe0analyzed-frame\xff\xd9"


def test_analyzed_stream_is_tracked_separately_from_raw(client, hub):
    h = register_and_login(client)
    hub.on_frame(JPEG)  # 원본만 오는 중 (analyzer 꺼짐)
    body = client.get(f"{API}/status", headers=h).json()
    assert body["online"] is True and body["analyzed"]["online"] is False

    hub.analyzed.on_frame(ANALYZED)
    body = client.get(f"{API}/status", headers=h).json()
    assert body["analyzed"]["online"] is True and body["analyzed"]["last_frame_age_seconds"] == 0.0
    assert hub.events == ["stream_online", "analysis_online"]


def test_snapshot_source_selects_raw_or_analyzed(client, hub):
    h = register_and_login(client)
    hub.on_frame(JPEG)
    assert client.get(f"{API}/snapshot?source=analyzed", headers=h).status_code == 404  # 분석 화면은 아직 없음
    hub.analyzed.on_frame(ANALYZED)
    assert client.get(f"{API}/snapshot?source=analyzed", headers=h).content == ANALYZED
    assert client.get(f"{API}/snapshot", headers=h).content == JPEG  # 기본은 원본
    assert client.get(f"{API}/snapshot?source=bogus", headers=h).status_code == 422


def test_mqtt_client_routes_each_topic_to_its_hub():
    pytest.importorskip("paho.mqtt.client")
    raw = service.CameraHub()
    analyzed = service.CameraHub()
    c = service.MqttCameraClient(raw, "mqtt://127.0.0.1:1", "t/raw", analyzed, "t/analyzed")
    msg = lambda topic, payload: type("M", (), {"topic": topic, "payload": payload})()  # noqa: E731
    c._on_message(None, None, msg("t/raw", JPEG))
    c._on_message(None, None, msg("t/analyzed", ANALYZED))
    c._on_message(None, None, msg("t/other", JPEG))  # 모르는 토픽은 무시
    assert raw.latest()[0] == JPEG and analyzed.latest()[0] == ANALYZED


# ---- 분석 정보 (analyzer가 JSON으로 보내는 횟수·각도·피드백) ------------------------

INFO = {
    "v": 1, "exercise": "squat", "calibrated": False, "elapsed_seconds": 12.3, "visible": True,
    "phase": "moving", "reps": 3,
    "primary": {"feature": "knee_angle", "value": 128.4},
    "features": {"knee_angle": 128.4, "trunk_lean": 31.0},
    "thresholds": {"up": 160, "count": 130, "target": 100},
    "live_feedback": ["trunk_lean"],
    "checks": [
        {"code": "trunk_lean", "feature": "trunk_lean", "stat": "max", "direction": "above", "limit": 55.0,
         "live": True, "tier": None, "value": 31.0, "severity": 0.2, "state": "ok"},
        {"code": "knee_low", "feature": "ankle_lift", "stat": "max", "direction": "below", "limit": 0.678,
         "live": False, "tier": "strong", "value": None, "severity": None, "state": "pending"},
    ],
    "last_rep": {"index": 3, "score": 82, "feedback": ["shallow_depth"], "primary_min": 112.0, "duration": 1.8},
    "rep_scores": [90, 70, 82],
}


def _info_bytes(**overrides):
    import json

    return json.dumps({**INFO, **overrides}).encode()


def test_analysis_requires_login_and_reports_offline_before_any_info(client, hub):
    assert client.get(f"{API}/analysis").status_code == 401
    h = register_and_login(client)
    assert client.get(f"{API}/analysis", headers=h).json() == {"online": False, "age_seconds": None, "info": None}


def test_analysis_returns_latest_info_and_goes_offline_when_analyzer_stops(client, hub):
    h = register_and_login(client)
    hub.info.on_info(_info_bytes(reps=2))
    hub.info.on_info(_info_bytes(reps=3))
    body = client.get(f"{API}/analysis", headers=h).json()
    assert body["online"] is True and body["info"]["reps"] == 3
    assert body["info"]["last_rep"]["score"] == 82 and body["info"]["live_feedback"] == ["trunk_lean"]
    assert body["info"]["thresholds"] == {"up": 160.0, "count": 130.0, "target": 100.0}
    assert [c["state"] for c in body["info"]["checks"]] == ["ok", "pending"]

    hub.clock.t += 6  # analyzer가 멈춤. 마지막 값은 남기되 오프라인으로 표시
    body = client.get(f"{API}/analysis", headers=h).json()
    assert body["online"] is False and body["info"]["reps"] == 3 and body["age_seconds"] == 6.0


def test_old_analyzer_without_checks_is_still_accepted_and_bad_states_rejected(client, hub):
    import json

    old = {k: v for k, v in INFO.items() if k != "checks"}
    hub.info.on_info(json.dumps({**old, "v": 1}).encode())
    assert hub.info.snapshot()["info"].checks == []
    bad = {**INFO, "checks": [{**INFO["checks"][0], "state": "great"}]}
    hub.info.on_info(json.dumps(bad).encode())  # 모르는 state 값은 통째로 무시하고 직전 값을 유지
    assert hub.info.snapshot()["info"].checks == []


def test_malformed_analysis_messages_are_ignored(client, hub):
    hub.info.on_info(b"not json")
    hub.info.on_info(b'{"v": 1}')  # 필수 항목 없음
    assert hub.info.snapshot()["info"] is None
    hub.info.on_info(_info_bytes())
    hub.info.on_info(b"garbage")  # 나쁜 메시지가 직전 정상 값을 지우면 안 된다
    assert hub.info.snapshot()["info"].reps == 3


def test_mqtt_client_routes_analysis_topic_to_info_hub():
    pytest.importorskip("paho.mqtt.client")
    raw, analyzed, info = service.CameraHub(), service.CameraHub(), service.InfoHub()
    c = service.MqttCameraClient(raw, "mqtt://127.0.0.1:1", "t/raw", analyzed, "t/analyzed", info, "t/analysis")
    c._on_message(None, None, type("M", (), {"topic": "t/analysis", "payload": _info_bytes()})())
    assert info.snapshot()["info"].exercise == "squat" and raw.latest()[0] is None


# ---- 웹에서 운동 선택 -> analyzer ---------------------------------------------------

class FakeControl:
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def publish_exercise(self, exercise):
        self.sent.append(exercise)
        return self.ok


def test_select_exercise_requires_login_and_valid_exercise(client, hub, monkeypatch):
    monkeypatch.setattr(service, "control", FakeControl())
    assert client.post(f"{API}/exercise", json={"exercise": "kneeup"}).status_code == 401
    h = register_and_login(client)
    assert client.post(f"{API}/exercise", json={"exercise": "jumping_jack"}, headers=h).status_code == 422
    assert client.post(f"{API}/exercise", json={}, headers=h).status_code == 422
    assert service.control.sent == []


def test_select_exercise_is_forwarded_to_the_analyzer(client, hub, monkeypatch):
    ctl = FakeControl()
    monkeypatch.setattr(service, "control", ctl)
    h = register_and_login(client)
    hub.info.on_info(_info_bytes())  # analyzer가 켜져 있다
    r = client.post(f"{API}/exercise", json={"exercise": "kneeup"}, headers=h)
    assert r.status_code == 202 and r.json() == {"requested": "kneeup"}
    assert ctl.sent == ["kneeup"]


def test_select_exercise_reports_why_it_cannot_be_delivered(client, hub, monkeypatch):
    h = register_and_login(client)
    body = {"exercise": "pushup"}
    monkeypatch.setattr(service, "control", None)  # 서버에 MQTT_URL이 없다
    assert client.post(f"{API}/exercise", json=body, headers=h).status_code == 503

    ctl = FakeControl()
    monkeypatch.setattr(service, "control", ctl)
    r = client.post(f"{API}/exercise", json=body, headers=h)  # analyzer가 꺼져 있다 -> 요청이 사라지지 않게 미리 알린다
    assert r.status_code == 409 and ctl.sent == []

    hub.info.on_info(_info_bytes())
    ctl.ok = False  # 브로커에 못 보냄
    assert client.post(f"{API}/exercise", json=body, headers=h).status_code == 503


def test_mqtt_client_publishes_control_message():
    pytest.importorskip("paho.mqtt.client")
    import json

    c = service.MqttCameraClient(service.CameraHub(), "mqtt://127.0.0.1:1", "t/raw", control_topic="t/control")
    sent = []
    c._client.publish = lambda topic, payload, qos=0: sent.append((topic, json.loads(payload), qos)) or type("I", (), {"rc": 0})()
    assert c.publish_exercise("kneeup") is True
    assert sent == [("t/control", {"exercise": "kneeup"}, 1)]
    assert service.MqttCameraClient(service.CameraHub(), "mqtt://127.0.0.1:1", "t/raw").publish_exercise("pushup") is False


# ---- 웹에서 운동 세션 시작/종료 -> analyzer ----------------------------------------

SESSION_IDLE = {"state": "idle", "id": None, "started_at": None, "last": None}
SESSION_ACTIVE = {"state": "active", "id": 9, "started_at": "2026-11-29T01:00:00Z", "last": None}


class SessionControl(FakeControl):
    def __init__(self, ok=True):
        super().__init__(ok)
        self.commands = []

    def publish_control(self, command):
        self.commands.append(command)
        return self.ok


def test_session_start_and_stop_are_forwarded_with_the_right_preconditions(client, hub, monkeypatch):
    ctl = SessionControl()
    monkeypatch.setattr(service, "control", ctl)
    assert client.post(f"{API}/session/stop").status_code == 401
    h = register_and_login(client)

    r = client.post(f"{API}/session/start", json={"exercise": "pushup"}, headers=h)
    assert r.status_code == 409 and ctl.commands == []  # analyzer가 꺼져 있다

    hub.info.on_info(_info_bytes(session=SESSION_IDLE))
    assert client.post(f"{API}/session/start", json={"exercise": "jumping"}, headers=h).status_code == 422
    assert client.post(f"{API}/session/stop", headers=h).status_code == 409  # 준비 중에는 끝낼 게 없다
    r = client.post(f"{API}/session/start", json={"exercise": "kneeup"}, headers=h)
    assert r.status_code == 202 and r.json() == {"requested": "start", "exercise": "kneeup"}
    assert ctl.commands == [{"action": "start", "exercise": "kneeup"}]

    hub.info.on_info(_info_bytes(session=SESSION_ACTIVE))
    assert client.post(f"{API}/session/start", json={"exercise": "kneeup"}, headers=h).status_code == 409
    r = client.post(f"{API}/session/stop", headers=h)
    assert r.status_code == 202 and r.json() == {"requested": "stop", "exercise": None}
    assert ctl.commands[-1] == {"action": "stop"}


def test_session_control_failure_modes(client, hub, monkeypatch):
    h = register_and_login(client)
    monkeypatch.setattr(service, "control", None)
    assert client.post(f"{API}/session/start", json={"exercise": "pushup"}, headers=h).status_code == 503
    assert client.post(f"{API}/session/stop", headers=h).status_code == 503

    ctl = SessionControl(ok=False)  # 브로커에 못 보냄
    monkeypatch.setattr(service, "control", ctl)
    hub.info.on_info(_info_bytes(session=SESSION_IDLE))
    assert client.post(f"{API}/session/start", json={"exercise": "pushup"}, headers=h).status_code == 503

    hub.info.on_info(_info_bytes())  # 세션을 모르는 예전 분석기(session 없음)
    r = client.post(f"{API}/session/start", json={"exercise": "pushup"}, headers=h)
    assert r.status_code == 409 and "update" in r.json()["detail"]


def test_analysis_exposes_the_session_state_and_the_last_finished_session(client, hub):
    h = register_and_login(client)
    last = {"id": 4, "exercise": "pushup", "total_reps": 8, "avg_score": 82.5, "saved": True}
    hub.info.on_info(_info_bytes(session={**SESSION_IDLE, "last": last}))
    info = client.get(f"{API}/analysis", headers=h).json()["info"]
    assert info["session"]["state"] == "idle" and info["session"]["last"] == {**last, "target_sets": None, "completed_sets": None}
    hub.info.on_info(_info_bytes())
    assert client.get(f"{API}/analysis", headers=h).json()["info"]["session"] is None


def test_mqtt_client_publish_control_sends_any_command_with_qos1():
    pytest.importorskip("paho.mqtt.client")
    import json

    c = service.MqttCameraClient(service.CameraHub(), "mqtt://127.0.0.1:1", "t/raw", control_topic="t/control")
    sent = []
    c._client.publish = lambda topic, payload, qos=0: sent.append((topic, json.loads(payload), qos)) or type("I", (), {"rc": 0})()
    assert c.publish_control({"action": "stop"}) is True
    assert sent == [("t/control", {"action": "stop"}, 1)]
