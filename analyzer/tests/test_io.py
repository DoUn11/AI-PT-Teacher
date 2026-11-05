import json
import logging
from types import SimpleNamespace

import cv2
import httpx
import numpy as np
import pytest

from aipt_analyzer import Analyzer, Judgement, Pose, build_judge, load_exercise_config, register_judge
from aipt_analyzer.estimators import create_estimator, pose_from_yolo_result
from aipt_analyzer.pipeline import run_pipeline
from aipt_analyzer.recorder import SessionRecorder
from aipt_analyzer.results import RepResult, RepSummary
from aipt_analyzer.sources import FileSource, LiveSource, open_source
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
