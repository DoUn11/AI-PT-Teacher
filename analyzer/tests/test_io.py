import json
import logging
import time
from types import SimpleNamespace

import cv2
import httpx
import numpy as np
import pytest

from aipt_analyzer import Analyzer, Judgement, Pose, build_judge, load_exercise_config, register_judge
from aipt_analyzer.estimators import create_estimator, pose_from_yolo_result
from aipt_analyzer.pipeline import PipelineParts, run_pipeline
from aipt_analyzer.recorder import SessionRecorder
from aipt_analyzer.results import RepResult, RepSummary
from aipt_analyzer.sources import FileSource, LiveSource, MqttSource, open_source, parse_mqtt_spec
from aipt_analyzer.uploader import BackendClient, UploadWorker
from tests.helpers import cycle, run, squat_pose


def make_rep(index=1, score=90, primary_min=95.0, feedback=()):
    summary = RepSummary(index, 0.0, 1.0, "knee_angle", primary_min, {"knee_angle": {"min": 95, "max": 170, "mean": 130}})
    return RepResult(summary, score, tuple(feedback))


# ---- 판정기 플러그인 -------------------------------------------------------


def test_custom_judge_can_be_plugged_in_via_config():
    @register_judge("always_fifty")
    class AlwaysFifty:
        def __init__(self, cfg):
            self.cfg = cfg

        def judge(self, summary):
            return Judgement(score=50, feedback=("custom",))

        def live_feedback(self, values):
            return []

    base = load_exercise_config("squat")
    cfg = type(base)(**{**base.__dict__, "judge": {"type": "always_fifty"}})
    frames = run(Analyzer(cfg), [squat_pose(k) for k in cycle(172, 90)])
    reps = [f.rep for f in frames if f.rep]
    assert [(r.score, r.feedback) for r in reps] == [(50, ("custom",))]


def test_unknown_judge_type_is_a_clear_error():
    base = load_exercise_config("squat")
    cfg = type(base)(**{**base.__dict__, "judge": {"type": "nope"}})
    with pytest.raises(ValueError, match="unknown judge type"):
        build_judge(cfg)


# ---- 추정기 ---------------------------------------------------------------


def _fake_result(n_people):
    xy = np.zeros((n_people, 17, 2))
    if n_people > 1:
        xy[1] += 7  # 두 번째 사람을 구분할 표식
    conf = np.full((n_people, 17), 0.9)
    boxes = np.array([[0, 0, 10, 10], [0, 0, 100, 200]][:n_people], dtype=float)
    return SimpleNamespace(keypoints=SimpleNamespace(xy=xy, conf=conf), boxes=SimpleNamespace(xyxy=boxes))


def test_yolo_result_picks_the_largest_person():
    pose = pose_from_yolo_result(_fake_result(2))
    assert pose.xy[0, 0] == 7  # 박스가 더 큰 두 번째 사람


def test_yolo_result_without_people_or_conf():
    assert pose_from_yolo_result(SimpleNamespace(keypoints=None, boxes=None)) is None
    empty = SimpleNamespace(keypoints=SimpleNamespace(xy=np.zeros((0, 17, 2)), conf=None), boxes=None)
    assert pose_from_yolo_result(empty) is None
    no_conf = _fake_result(1)
    no_conf.keypoints.conf = None
    assert pose_from_yolo_result(no_conf).conf.min() == 1.0


def test_unknown_estimator_is_a_clear_error():
    with pytest.raises(ValueError, match="unknown estimator"):
        create_estimator("nope")


# ---- 영상 소스 ------------------------------------------------------------


@pytest.fixture()
def video(tmp_path):
    path = str(tmp_path / "v.avi")
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 48))
    for i in range(5):
        w.write(np.full((48, 64, 3), i * 40, np.uint8))
    w.release()
    return path


def test_file_source_reads_all_frames_then_exhausts(video):
    src = open_source(video)
    assert isinstance(src, FileSource)
    times = []
    while (item := src.read()) is not None:
        times.append(item[1])
    assert src.exhausted and len(times) == 5
    assert times == pytest.approx([0, 0.1, 0.2, 0.3, 0.4])
    src.close()


def test_missing_video_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_source(str(tmp_path / "missing.avi"))


def test_live_source_returns_latest_frame_and_closes(video):
    src = LiveSource(video)
    item = src.read(timeout=5.0)
    assert item is not None and item[0].shape == (48, 64, 3)
    src.close()  # 스레드가 멈춰야 한다
    assert not src._thread.is_alive()


# ---- MQTT 소스 -------------------------------------------------------------


def test_parse_mqtt_spec_defaults_and_tls():
    assert parse_mqtt_spec("mqtt://pi.local") == ("pi.local", 1883, "aipt/camera/frame", False)
    assert parse_mqtt_spec("mqtt://10.0.0.5:1884/a/b/frame") == ("10.0.0.5", 1884, "a/b/frame", False)
    assert parse_mqtt_spec("mqtts://broker/x") == ("broker", 8883, "x", True)
    with pytest.raises(ValueError, match="MQTT 주소"):
        parse_mqtt_spec("tcp://10.0.0.5:8888")


def test_mqtt_source_keeps_only_latest_frame_and_ignores_garbage():
    pytest.importorskip("paho.mqtt.client")
    src = open_source("mqtt://127.0.0.1:1/test/frame")  # 브로커 없이도 만들어져야 한다 (재시도만 함)
    try:
        assert isinstance(src, MqttSource)
        assert src.read(timeout=0.05) is None

        def jpeg(value):
            ok, buf = cv2.imencode(".jpg", np.full((48, 64, 3), value, np.uint8))
            return SimpleNamespace(payload=buf.tobytes())

        src._on_message(None, None, SimpleNamespace(payload=b"not a jpeg"))
        assert src.read(timeout=0.05) is None
        src._on_message(None, None, jpeg(30))
        src._on_message(None, None, jpeg(200))  # 소비 전에 두 장이 쌓이면 최신만 남는다
        frame, _ = src.read(timeout=1.0)
        assert frame.shape == (48, 64, 3) and frame.mean() > 150
        assert src.read(timeout=0.05) is None
    finally:
        src.close()


def test_mqtt_source_reports_rx_status_and_warns_when_silent(caplog):
    pytest.importorskip("paho.mqtt.client")
    caplog.set_level(logging.INFO, logger="aipt.mqtt")
    src = MqttSource("mqtt://127.0.0.1:1/x/frame", report_interval=0.2)
    try:
        time.sleep(0.5)  # 브로커가 없으니 '연결되지 않았습니다' 경고
        assert any("연결되지 않았습니다" in r.message for r in caplog.records)

        ok, buf = cv2.imencode(".jpg", np.zeros((48, 64, 3), np.uint8))
        src._connected = True
        caplog.clear()
        for _ in range(3):
            src._on_message(None, None, SimpleNamespace(payload=buf.tobytes()))
        time.sleep(0.3)
        assert any("수신 중" in r.message and "fps" in r.message for r in caplog.records)

        caplog.clear()
        time.sleep(0.5)  # 연결은 됐는데 프레임이 안 오면 토픽 확인을 안내
        assert any("프레임이 없습니다" in r.message and "x/frame" in r.message for r in caplog.records)
    finally:
        src.close()


# ---- 파이프라인 + 기록 -----------------------------------------------------


class FakeSource:
    def __init__(self, n):
        self._n, self._i, self.exhausted = n, 0, False

    def read(self, timeout=1.0):
        if self._i >= self._n:
            self.exhausted = True
            return None
        self._i += 1
        return np.zeros((48, 64, 3), np.uint8), (self._i - 1) / 30

    def close(self):
        pass


class FakeEstimator:
    def __init__(self, poses):
        self._it = iter(poses)

    def estimate(self, frame):
        return next(self._it)


class FakeWorker:
    def __init__(self):
        self.reps = []

    def submit(self, rep):
        self.reps.append(rep)


def test_pipeline_records_and_submits_each_rep(tmp_path):
    cfg = load_exercise_config("squat")
    poses = [squat_pose(k, lean=20) for _ in range(2) for k in cycle(172, 90)]
    worker, seen = FakeWorker(), []
    recorder = SessionRecorder(tmp_path, cfg)

    reps = run_pipeline(
        Analyzer(cfg), FakeSource(len(poses)), FakeEstimator(poses),
        recorder=recorder, worker=worker, on_rep=seen.append,
    )
    recorder.close()

    assert reps == 2 and [r.summary.index for r in worker.reps] == [1, 2] == [r.summary.index for r in seen]

    meta = json.loads((recorder.dir / "meta.json").read_text())
    assert meta["exercise"] == "squat" and meta["config"]["version"] == cfg.version
    assert meta["config"]["depth"]["target"] == cfg.depth.target

    frames = [json.loads(line) for line in (recorder.dir / "frames.jsonl").read_text().splitlines()]
    assert len(frames) == len(poses)
    assert "knee_angle" in frames[0]["features"]
    assert Pose.from_rows(frames[0]["keypoints"]).xy.shape == (17, 2)  # 원본 키포인트로 복원 가능

    rep_rows = [json.loads(line) for line in (recorder.dir / "reps.jsonl").read_text().splitlines()]
    assert [r["index"] for r in rep_rows] == [1, 2]
    assert {"score", "feedback", "stats", "primary_min"} <= rep_rows[0].keys()


def test_pipeline_stops_after_seconds_for_endless_source():
    class Endless(FakeSource):
        def read(self, timeout=1.0):
            return np.zeros((48, 64, 3), np.uint8), 0.0

    cfg = load_exercise_config("squat")
    run_pipeline(Analyzer(cfg), Endless(0), FakeEstimator(iter(lambda: None, 1)), seconds=0.05)


def test_pipeline_hands_annotated_frames_to_callback_without_a_window():
    class FakeOverlay:
        def render(self, frame, result, now, text=True):
            self.calls.append(text)
            return frame + 1  # 오버레이가 그려진 프레임이라는 표식

        calls: list = []

    cfg = load_exercise_config("squat")
    poses = [squat_pose(k) for k in cycle(172, 90)]
    got, results = [], []
    overlay = FakeOverlay()
    run_pipeline(
        Analyzer(cfg), FakeSource(len(poses)), FakeEstimator(poses),
        overlay=overlay, show=False, on_annotated=got.append, on_result=results.append,
    )  # show=False면 cv2.imshow/waitKey를 부르지 않는다 (창 없는 환경에서도 동작)
    assert len(got) == len(poses) and got[0].mean() == 1
    assert set(overlay.calls) == {False}  # 웹으로 내보내는 프레임에는 글자를 쓰지 않는다
    assert len(results) == len(poses)


def test_overlay_without_text_draws_nothing_when_no_person():
    from aipt_analyzer.overlay import Overlay
    from aipt_analyzer.results import FrameResult

    ov = Overlay("squat", {}, "knee_angle", font_path=None)
    blank = np.zeros((240, 320, 3), np.uint8)
    res = FrameResult(visible=False, pose=None)
    assert ov.render(blank, res, 0.0).any()  # 글자 모드: '사람이 보이지 않아요' 문구가 그려진다
    assert not ov.render(blank, res, 0.0, text=False).any()  # 선 모드: 아무 글자도 없다


def test_analysis_publisher_payload_contract():
    pytest.importorskip("paho.mqtt.client")
    from aipt_analyzer.publisher import AnalysisPublisher

    cfg = load_exercise_config("squat")
    an = Analyzer(cfg)
    pub = AnalysisPublisher("mqtt://127.0.0.1:1/x/analysis", cfg, rate_hz=1000)
    try:
        results = run(an, [squat_pose(k, lean=20) for k in cycle(172, 90)])
        payloads = [pub.payload(r, now=10.0) for r in results]
        last = payloads[-1]
        assert last["v"] == 4 and last["exercise"] == "squat" and last["reps"] == 1
        assert last["primary"]["feature"] == "knee_angle" and isinstance(last["primary"]["value"], float)
        assert last["thresholds"] == {"up": 160, "count": 130, "target": 100}
        assert last["last_rep"]["index"] == 1 and last["rep_scores"] == [last["last_rep"]["score"]]
        assert payloads[0]["last_rep"] is None and payloads[0]["rep_scores"] == []
        import json
        json.dumps(last)  # JSON으로 직렬화 가능해야 한다
        assert pub.update(results[0]) is False  # 브로커가 없으면 보내지 않고 조용히 건너뛴다
    finally:
        pub.close()


def test_analysis_payload_follows_new_calibrated_config_semantics():
    """새 분석기(보정된 설정): 비율 특징, 반복 후 판정(live=False), 꺼 둔 조건(enabled=False)."""
    pytest.importorskip("paho.mqtt.client")
    import json

    from aipt_analyzer.publisher import AnalysisPublisher
    from tests.helpers import frontal_pose

    cfg = load_exercise_config("sidecrunch")
    pub = AnalysisPublisher("mqtt://127.0.0.1:1/x/analysis", cfg, rate_hz=1000)
    try:
        an = Analyzer(cfg)
        res = an.process(frontal_pose(knee_h=-0.7), 0.0)
        p = pub.payload(res, now=1.0)
        by_code = {c["code"]: c for c in p["checks"]}
        assert "gaze_front" not in by_code  # enabled=False인 조건은 보내지 않는다
        assert set(by_code) == {c.code for c in cfg.form_checks if c.enabled}
        assert by_code["knee_side"]["live"] is False and by_code["knee_side"]["state"] == "pending"  # 아직 반복 없음
        assert by_code["hands_behind_head"]["live"] is True and by_code["hands_behind_head"]["value"] is not None
        assert by_code["hands_behind_head"]["direction"] == "above"
        assert p["primary"]["feature"] == "knee_gap" and 0.5 < p["primary"]["value"] < 1.0  # 비율. 소수점이 살아 있다
        json.dumps(p)

    finally:
        pub.close()

    # 니업: 무릎을 덜 올린 반복 -> 반복 후 판정 조건(knee_low, live=False)이 마지막 반복의 값으로 채워지고 bad가 된다
    kcfg = load_exercise_config("kneeup")
    kpub = AnalysisPublisher("mqtt://127.0.0.1:1/x/analysis", kcfg, rate_hz=1000)
    try:
        an = Analyzer(kcfg)
        results = run(an, [frontal_pose(k) for k in cycle(-0.9, -0.38)])
        before = {c["code"]: c for c in kpub.payload(results[0], now=1.0)["checks"]}
        assert before["knee_low"]["state"] == "pending" and before["knee_low"]["value"] is None
        rep = next(r for r in results if r.rep)
        after = kpub.payload(rep, now=2.0)
        knee_low = {c["code"]: c for c in after["checks"]}["knee_low"]
        assert knee_low["state"] == "bad" and knee_low["value"] is not None and knee_low["severity"] >= 0.5
        assert after["last_rep"]["feedback"] == ["knee_low"] and after["last_rep"]["score"] == 70
        assert after["thresholds"]["target"] == 0.58  # 비율 기준선이 소수점 그대로 간다
    finally:
        kpub.close()


def test_frame_publisher_encodes_jpeg_and_never_raises_without_broker():
    pytest.importorskip("paho.mqtt.client")
    from aipt_analyzer.publisher import FramePublisher

    pub = FramePublisher("mqtt://127.0.0.1:1/x/analyzed")  # 브로커 없음
    try:
        assert pub.publish(np.zeros((48, 64, 3), np.uint8)) is False  # 연결 전: 건너뛴다
        assert pub.dropped == 1 and pub.sent == 0
    finally:
        pub.close()


def test_pipeline_switch_swaps_all_parts_mid_stream():
    from aipt_analyzer.results import FrameResult

    class FakeAnalyzer:
        def __init__(self, name, log):
            self.name, self.log = name, log
            self.counter = type("C", (), {"reps": 0})()

        def process(self, pose, t):
            self.log.append(self.name)
            return FrameResult(visible=False, pose=None)

    log, switched = [], {"n": 0}
    first, second = FakeAnalyzer("a", log), FakeAnalyzer("b", log)
    new_worker = FakeWorker()

    def switch():
        switched["n"] += 1
        return PipelineParts(second, None, None, new_worker) if switched["n"] == 4 else None

    run_pipeline(first, FakeSource(8), FakeEstimator(iter(lambda: None, 1)), switch=switch)
    assert log == ["a"] * 3 + ["b"] * 5  # 4번째 프레임부터 새 분석기가 처리한다
    assert switched["n"] >= 8  # 프레임마다 물어본다


def test_exercise_control_keeps_latest_valid_request_only():
    pytest.importorskip("paho.mqtt.client")
    from aipt_analyzer.control import ExerciseControl

    ctl = ExerciseControl("mqtt://127.0.0.1:1/x/control")  # 브로커 없이도 만들어진다
    msg = lambda payload, retain=False: SimpleNamespace(payload=payload, retain=retain)  # noqa: E731
    try:
        assert ctl.take() is None
        ctl._on_message(None, None, msg(b'{"exercise": "kneeup"}'))
        ctl._on_message(None, None, msg(b'{"exercise": "pushup"}'))
        assert ctl.take() == ("select", "pushup", None)  # 가장 최근 요청
        assert ctl.take() is None  # 한 번 가져가면 비워진다
        for bad in (b"not json", b'{"exercise": "nope"}', b'{"x": 1}', b"[1]"):
            ctl._on_message(None, None, msg(bad))
        ctl._on_message(None, None, msg(b'{"exercise": "kneeup"}', retain=True))  # 남아 있던 옛 요청
        assert ctl.take() is None
    finally:
        ctl.close()


def test_analysis_publisher_restarts_counts_when_exercise_changes():
    pytest.importorskip("paho.mqtt.client")
    from aipt_analyzer.publisher import AnalysisPublisher

    cfg = load_exercise_config("squat")
    pub = AnalysisPublisher("mqtt://127.0.0.1:1/x/analysis", cfg, rate_hz=1000)
    try:
        results = run(Analyzer(cfg), [squat_pose(k, lean=20) for k in cycle(172, 90)])
        done = next(r for r in results if r.rep)
        before = pub.payload(done, now=5.0)
        assert before["reps"] == 1 and before["rep_scores"] and before["last_rep"]
        new_cfg = load_exercise_config("kneeup")
        pub.set_config(new_cfg)
        fresh = pub.payload(Analyzer(new_cfg).process(None, 0.0), now=6.0)
        assert fresh["exercise"] == "kneeup" and fresh["reps"] == 0
        assert fresh["last_rep"] is None and fresh["rep_scores"] == []  # 이전 운동 기록이 남지 않는다
        assert fresh["primary"]["feature"] == "knee_gap" and fresh["thresholds"]["target"] == 0.58
        assert {c["code"] for c in fresh["checks"]} == {c.code for c in new_cfg.form_checks if c.enabled}
    finally:
        pub.close()


def test_starting_a_new_session_drops_pending_reps_of_the_previous_exercise():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(201, json={"id": len(calls)})

    client = BackendClient("http://x", "a@b.c", "pw", client=httpx.Client(transport=httpx.MockTransport(handler)))
    client._token = "t"
    client.start_session("pushup")
    client.enqueue(make_rep(1))
    assert client.pending_count == 1  # 못 보낸 rep이 남은 채로
    client.start_session("kneeup")  # 운동을 바꿔 새 세션
    assert client.pending_count == 0 and client.session_id == 2


# ---- 업로더 ---------------------------------------------------------------


class Backend:
    """MockTransport 핸들러. 요청을 기록하고 상태를 시나리오별로 바꿀 수 있다."""

    def __init__(self):
        self.requests = []
        self.logins = 0
        self.reps_status = [201]  # 호출마다 하나씩 소비, 다 쓰면 마지막 값 반복
        self.reject_old_token = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/auth/login"):
            self.logins += 1
            return httpx.Response(200, json={"access_token": f"tok{self.logins}"})
        if path.endswith("/workouts/sessions"):
            return httpx.Response(201, json={"id": 7})
        if path.endswith("/reps"):
            if self.reject_old_token and request.headers["authorization"] == "Bearer tok1":
                return httpx.Response(401, json={"detail": "expired"})
            status = self.reps_status.pop(0) if len(self.reps_status) > 1 else self.reps_status[0]
            if status == "network":
                raise httpx.ConnectError("down")
            return httpx.Response(status, json={"accepted": 1, "duplicates": 0})
        if path.endswith("/finish"):
            return httpx.Response(200, json={})
        return httpx.Response(404)


def make_client(backend):
    http = httpx.Client(transport=httpx.MockTransport(backend))
    c = BackendClient("http://api.test/", "a@example.com", "pw", client=http)
    c.login()
    c.start_session("squat")
    return c


def reps_bodies(backend):
    return [json.loads(r.content) for r in backend.requests if r.url.path.endswith("/reps")]


def test_upload_payload_matches_backend_contract():
    backend = Backend()
    c = make_client(backend)
    c.enqueue(make_rep(2, 77, 118.456, ["shallow_depth"]))
    assert c.flush() and c.pending_count == 0

    (body,) = reps_bodies(backend)
    rep = body["reps"][0]
    assert rep["index"] == 2 and rep["score"] == 77 and rep["min_angle"] == 118.46
    assert rep["feedback"] == ["shallow_depth"]
    assert rep["completed_at"].endswith("+00:00")  # 백엔드는 시간대 없는 시각을 거부한다
    assert backend.requests[-1].url.path == "/api/v1/workouts/sessions/7/reps"


def test_network_failure_keeps_reps_and_retries_in_one_batch():
    backend = Backend()
    backend.reps_status = ["network", 201]
    c = make_client(backend)
    c.enqueue(make_rep(1))
    assert c.flush() is False and c.pending_count == 1
    c.enqueue(make_rep(2))
    assert c.flush() is True and c.pending_count == 0
    assert [r["index"] for r in reps_bodies(backend)[-1]["reps"]] == [1, 2]


def test_expired_token_triggers_one_relogin():
    backend = Backend()
    backend.reject_old_token = True
    c = make_client(backend)
    c.enqueue(make_rep(1))
    assert c.flush() is True
    assert backend.logins == 2


def test_permanent_rejection_drops_batch_instead_of_retrying_forever(caplog):
    backend = Backend()
    backend.reps_status = [409]
    c = make_client(backend)
    c.enqueue(make_rep(1))
    with caplog.at_level(logging.ERROR, logger="aipt.upload"):
        assert c.flush() is True
    assert c.pending_count == 0 and "거부" in caplog.text


def test_server_error_is_retried_later():
    backend = Backend()
    backend.reps_status = [503]
    c = make_client(backend)
    c.enqueue(make_rep(1))
    assert c.flush() is False and c.pending_count == 1


def test_worker_flushes_in_background_and_finishes_session_on_close():
    backend = Backend()
    c = make_client(backend)
    worker = UploadWorker(c, retry_interval=0.05).start()
    worker.submit(make_rep(1))
    worker.submit(make_rep(2))
    worker.close()
    sent = [rep["index"] for body in reps_bodies(backend) for rep in body["reps"]]
    assert sorted(set(sent)) == [1, 2]
    assert backend.requests[-1].url.path.endswith("/sessions/7/finish")
