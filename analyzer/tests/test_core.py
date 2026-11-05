import json

import numpy as np
import pytest

from aipt_analyzer import Analyzer, ExerciseConfig, load_exercise_config
from aipt_analyzer.config import available_exercises
from aipt_analyzer.features import extract_features
from aipt_analyzer.pose import Pose, angle_deg, vertical_lean_deg
from tests.helpers import cycle, pushup_pose, run, squat_pose


def reps_of(frames):
    return [f.rep for f in frames if f.rep is not None]


# ---- 기하/특징 -------------------------------------------------------------


def test_angle_and_lean_geometry():
    assert angle_deg((0, 1), (0, 0), (1, 0)) == pytest.approx(90)
    assert angle_deg((1, 0), (0, 0), (-1, 0)) == pytest.approx(180)
    assert angle_deg((0, 0), (0, 0), (1, 0)) is None  # 점이 겹침
    assert vertical_lean_deg((0, 10), (0, 0)) == pytest.approx(0)  # 위로 곧게
    assert vertical_lean_deg((0, 10), (10, 0)) == pytest.approx(45)


def test_extract_features_recovers_known_angles():
    f = extract_features(squat_pose(knee=120, lean=30))
    assert f.side == "left"  # 왼쪽 몸만 신뢰도가 높음
    assert f.values["knee_angle"] == pytest.approx(120, abs=0.01)
    assert f.values["trunk_lean"] == pytest.approx(30, abs=0.01)
    assert "elbow_angle" not in f.values  # 팔은 안 보이니 계산하지 않음


def test_features_skip_low_confidence_joints():
    pose = squat_pose(knee=120)
    conf = pose.conf.copy()
    conf[13] = 0.1  # left_knee
    f = extract_features(Pose(pose.xy, conf))
    assert "knee_angle" not in f.values


def test_pose_row_roundtrip_and_validation():
    pose = squat_pose(100)
    again = Pose.from_rows(pose.to_rows())
    assert np.allclose(again.xy, pose.xy, atol=0.1)
    with pytest.raises(ValueError):
        Pose.from_rows([[0, 0, 1]] * 5)


# ---- 설정 -----------------------------------------------------------------


def test_builtin_configs_load():
    assert {"squat", "pushup"} <= set(available_exercises())
    for name in ("squat", "pushup"):
        cfg = load_exercise_config(name)
        assert cfg.exercise == name and cfg.calibrated is False


def _cfg_dict(**overrides):
    d = json.loads(json.dumps(load_exercise_config("squat").__dict__, default=lambda o: o.__dict__))
    d.update(overrides)
    return d


@pytest.mark.parametrize(
    "overrides",
    [
        {"count_threshold": 170},  # up_threshold(160)보다 큼
        {"smoothing": 0},
        {"primary_feature": "nonexistent"},
        {"form_checks": [{"code": "x", "feature": "nope", "stat": "max", "limit": 1, "penalty": 1}]},
        {"form_checks": [{"code": "x", "feature": "trunk_lean", "stat": "avg", "limit": 1, "penalty": 1}]},
    ],
)
def test_invalid_config_rejected(overrides):
    with pytest.raises(ValueError):
        ExerciseConfig.from_dict(_cfg_dict(**overrides))


def test_missing_config_key_is_a_clear_error():
    with pytest.raises(ValueError, match="invalid exercise config"):
        ExerciseConfig.from_dict({"exercise": "x"})


def test_override_path_replaces_builtin_values(tmp_path):
    d = _cfg_dict(count_threshold=140, version=7, calibrated=True)
    p = tmp_path / "learned.json"
    p.write_text(json.dumps(d))
    cfg = load_exercise_config("squat", override_path=p)
    assert (cfg.count_threshold, cfg.version, cfg.calibrated) == (140, 7, True)


# ---- 스쿼트 ---------------------------------------------------------------


def test_good_squats_are_counted_and_scored_high():
    a = Analyzer(load_exercise_config("squat"))
    poses = [squat_pose(k, lean=20) for _ in range(3) for k in cycle(172, 90)]
    frames = run(a, poses)
    reps = reps_of(frames)
    assert [r.summary.index for r in reps] == [1, 2, 3]
    assert frames[-1].reps == 3
    for r in reps:
        assert r.score >= 95 and r.feedback == ()
        assert r.summary.primary_min == pytest.approx(90, abs=3)  # 스무딩으로 약간 어긋남


def test_shallow_squat_counts_but_gets_feedback_and_lower_score():
    a = Analyzer(load_exercise_config("squat"))
    reps = reps_of(run(a, [squat_pose(k) for k in cycle(172, 125)]))
    assert len(reps) == 1
    assert "shallow_depth" in reps[0].feedback
    assert reps[0].score < 70


def test_excessive_trunk_lean_is_flagged():
    a = Analyzer(load_exercise_config("squat"))
    reps = reps_of(run(a, [squat_pose(k, lean=70) for k in cycle(172, 90)]))
    assert len(reps) == 1
    assert reps[0].feedback == ("trunk_lean",)
    assert reps[0].score == 85


def test_partial_dip_that_never_reaches_count_threshold_is_not_a_rep():
    a = Analyzer(load_exercise_config("squat"))
    frames = run(a, [squat_pose(k) for k in cycle(172, 145)])
    assert reps_of(frames) == [] and frames[-1].reps == 0


def test_fast_jitter_is_not_a_rep():
    a = Analyzer(load_exercise_config("squat"))
    # 0.2초 만에 오르내리는 떨림: min_rep_seconds(0.6) 미만
    assert reps_of(run(a, [squat_pose(k) for k in cycle(172, 90, frames=6)])) == []


def test_waits_for_standing_pose_before_counting():
    a = Analyzer(load_exercise_config("squat"))
    frames = run(a, [squat_pose(100)] * 5)  # 처음부터 앉아 있음
    assert {f.phase for f in frames} == {"waiting"}
    assert reps_of(frames) == []


def test_lost_visibility_frames_do_not_break_counting():
    a = Analyzer(load_exercise_config("squat"))
    poses = [squat_pose(k) for k in cycle(172, 90)]
    poses[10:14] = [None] * 4  # 중간에 사람이 잠깐 안 보임
    frames = run(a, poses)
    assert [f.visible for f in frames[10:14]] == [False] * 4
    assert len(reps_of(frames)) == 1


def test_live_feedback_only_while_moving():
    a = Analyzer(load_exercise_config("squat"))
    frames = run(a, [squat_pose(k, lean=70) for k in cycle(172, 90)])
    assert frames[0].live_feedback == []  # 서 있을 때는 없음
    assert any("trunk_lean" in f.live_feedback for f in frames)


def test_start_count_continues_numbering():
    a = Analyzer(load_exercise_config("squat"), start_count=4)
    reps = reps_of(run(a, [squat_pose(k) for k in cycle(172, 90)]))
    assert reps[0].summary.index == 5


# ---- 푸시업 ---------------------------------------------------------------


def test_pushups_counted_with_body_line_check():
    cfg = load_exercise_config("pushup")
    good = reps_of(run(Analyzer(cfg), [pushup_pose(e) for e in cycle(170, 80)]))
    assert len(good) == 1 and good[0].feedback == () and good[0].score >= 95

    sag = reps_of(run(Analyzer(cfg), [pushup_pose(e, hip_offset=60) for e in cycle(170, 80)]))
    assert len(sag) == 1 and "body_line" in sag[0].feedback and sag[0].score <= 80
