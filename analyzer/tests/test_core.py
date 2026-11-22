import json

import numpy as np
import pytest

from aipt_analyzer import Analyzer, ExerciseConfig, load_exercise_config
from aipt_analyzer.config import available_exercises
from aipt_analyzer.features import extract_features
from aipt_analyzer.pose import Pose, angle_deg, vertical_lean_deg
from tests.helpers import cycle, frontal_pose, pushup_pose, run, squat_pose


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
    assert {"squat", "pushup", "kneeup", "sidecrunch"} <= set(available_exercises())
    for name in ("squat", "pushup", "kneeup", "sidecrunch"):
        cfg = load_exercise_config(name)
        assert cfg.exercise == name
    assert load_exercise_config("squat").calibrated is False  # 스쿼트는 데이터 보정 대상이 아니다
    for name in ("pushup", "kneeup", "sidecrunch"):  # calibration/calibrate.py가 만든 설정
        cfg = load_exercise_config(name)
        assert cfg.calibrated is True and cfg.score_step == 10
        gaze = [c for c in cfg.form_checks if c.code in ("gaze_front", "leg_tension")]
        assert all(not c.enabled for c in gaze)  # 시선, 다리 긴장은 꺼 둔다 (HANDOFF.md 결정)


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


def test_pushups_counted_and_shallow_ones_penalised_lightly():
    cfg = load_exercise_config("pushup")
    good = reps_of(run(Analyzer(cfg), [pushup_pose(e) for e in cycle(170, 80)]))
    assert len(good) == 1 and good[0].feedback == () and good[0].score == 100

    # 몸 일직선(body_line)은 정면 카메라에서 판정하지 않으므로 꺼져 있다
    sag = reps_of(run(Analyzer(cfg), [pushup_pose(e, hip_offset=60) for e in cycle(170, 80)]))
    assert len(sag) == 1 and sag[0].feedback == ()

    # 얕은 푸시업도 1회로 세고(count_threshold 140), 약한 조건이라 조금만 깎는다.
    # 2초짜리 동작: 1초면 150도 아래에 머무는 시간이 min_rep_seconds(0.6초)보다 짧아 떨림으로 버려진다
    shallow = reps_of(run(Analyzer(cfg), [pushup_pose(e) for e in cycle(170, 136, frames=60)]))
    assert len(shallow) == 1 and shallow[0].feedback == ("shallow_depth",) and shallow[0].score == 90


# ---- 약한 조건 보정 칸 (enabled, live, full_at, stat mean, score_step) ----
# 프론트엔드 frontend/tests/analysis.test.js 의 같은 이름 테스트와 숫자가 같아야 한다.

from aipt_analyzer.judges import ThresholdJudge  # noqa: E402
from aipt_analyzer.results import RepSummary  # noqa: E402


def _lean(**more):
    return {"code": "trunk_lean", "feature": "trunk_lean", "stat": "max", "limit": 40, "penalty": 20, **more}


def _judge_with(checks, **extra):
    return ThresholdJudge(ExerciseConfig.from_dict(_cfg_dict(form_checks=checks, **extra)))


def _rep_at(lean):
    return RepSummary(index=1, start_t=0, end_t=1, primary_feature="knee_angle", primary_min=90,
                      stats={"trunk_lean": {"min": lean, "max": lean, "mean": lean}})


def test_disabled_check_is_not_judged_penalised_or_shown_live():
    j = _judge_with([_lean(enabled=False)])
    r = j.judge(_rep_at(80))
    assert (r.score, r.feedback) == (100, ())
    assert j.live_feedback({"trunk_lean": 80}) == []


def test_live_false_keeps_after_rep_feedback_but_drops_live_warning():
    j = _judge_with([_lean(live=False)])
    r = j.judge(_rep_at(50))
    assert (r.score, r.feedback) == (80, ("trunk_lean",))
    assert j.live_feedback({"trunk_lean": 50}) == []


def test_full_at_grades_penalty_by_distance_past_limit():
    j = _judge_with([_lean(full_at=60)])
    assert [j.judge(_rep_at(x)).score for x in (45, 50, 90, 40)] == [95, 90, 80, 100]


def test_direction_flips_which_side_of_limit_is_a_violation():
    j = _judge_with([_lean(direction="below")])  # 최댓값이 40에 못 미치면 위반
    assert (j.judge(_rep_at(30)).score, j.judge(_rep_at(30)).feedback) == (80, ("trunk_lean",))
    assert j.judge(_rep_at(50)).score == 100


def test_stat_mean_uses_direction():
    j = _judge_with([{"code": "low", "feature": "trunk_lean", "stat": "mean", "direction": "below",
                      "limit": 10, "penalty": 30}])
    assert (j.judge(_rep_at(5)).score, j.judge(_rep_at(5)).feedback) == (70, ("low",))
    assert j.judge(_rep_at(15)).score == 100


def test_score_step_rounds_rep_score_to_10_point_steps():
    j = _judge_with([_lean(full_at=60)], score_step=10)
    assert [j.judge(_rep_at(x)).score for x in (44, 46, 50, 90)] == [100, 90, 90, 80]


@pytest.mark.parametrize(
    "check,extra",
    [
        (_lean(full_at=30), {}),  # max 조건인데 full_at이 limit보다 작음
        (_lean(stat="mean"), {}),  # mean인데 direction 없음
        (_lean(direction="sideways"), {}),
        (_lean(tier="great"), {}),
        (_lean(penalty=-1), {}),
        (_lean(), {"score_step": 0}),
    ],
)
def test_invalid_weak_condition_fields_rejected(check, extra):
    with pytest.raises(ValueError):
        ExerciseConfig.from_dict(_cfg_dict(form_checks=[check], **extra))


def test_weak_condition_fields_accepted():
    cfg = ExerciseConfig.from_dict(_cfg_dict(form_checks=[_lean(tier="weak", live=False, full_at=60)], score_step=10))
    assert cfg.score_step == 10 and cfg.form_checks[0].live is False


# ---- 정면 촬영용 양쪽 몸·얼굴 특징, 니업 ------------------------------------
# 숫자는 frontend/tests/analysis.test.js 의 같은 이름 테스트와 같아야 한다.


def test_body_features_on_frontal_pose():
    v = extract_features(frontal_pose(-0.9)).values
    expected = {"torso_sw": 2.5, "knee_gap": 0.9, "hip_flex": 177.137595, "knee_lat": 0.0, "ankle_lift": 0.0,
                "head_pitch": 0.02, "head_yaw": 0.0, "elbow_angle_both": 175.030259,
                "shoulder_angle_both": 14.172338, "hand_ratio": 1.75}
    for k, x in expected.items():
        assert v[k] == pytest.approx(x, abs=1e-5), k
    lifted = extract_features(frontal_pose(0.1)).values
    assert lifted["knee_gap"] == pytest.approx(-0.1) and lifted["ankle_lift"] == pytest.approx(1.0)


def test_body_features_skip_when_face_not_visible():
    pose = frontal_pose()
    conf = pose.conf.copy()
    conf[0] = 0.1  # nose
    v = extract_features(Pose(pose.xy, conf)).values
    assert "head_pitch" not in v and "head_yaw" not in v and "torso_sw" in v


def test_kneeup_counts_lifts_and_flags_low_knee():
    a = Analyzer(load_exercise_config("kneeup"))
    good = reps_of(run(a, [frontal_pose(k) for _ in range(2) for k in cycle(-0.9, 0.0)]))
    assert len(good) == 2 and all(r.score == 100 and r.feedback == () for r in good)

    a = Analyzer(load_exercise_config("kneeup"))
    low = reps_of(run(a, [frontal_pose(k) for k in cycle(-0.9, -0.38)]))
    assert len(low) == 1  # 무릎이 덜 올라와도 1회로 센다
    assert low[0].feedback == ("knee_low",) and low[0].score == 70
