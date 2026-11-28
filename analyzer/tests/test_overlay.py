import numpy as np
import pytest

from aipt_analyzer import Analyzer, load_exercise_config
from aipt_analyzer.config import load_messages
from aipt_analyzer.overlay import Overlay
from tests.helpers import cycle, run, squat_pose


def _overlay(font_path=None):
    return Overlay("squat", load_messages(), "knee_angle", font_path=font_path)


def _frames():
    cfg = load_exercise_config("squat")
    a = Analyzer(cfg)
    return run(a, [squat_pose(k, lean=70) for k in cycle(172, 90)])


def test_render_draws_on_a_copy_and_keeps_frame_shape():
    blank = np.zeros((480, 640, 3), np.uint8)
    result = _frames()[10]
    out = _overlay().render(blank, result, now=0.0)
    assert out.shape == blank.shape
    assert out.any() and not blank.any()  # 원본은 건드리지 않음


def test_render_works_without_a_font_and_without_a_person():
    from aipt_analyzer.results import FrameResult

    blank = np.zeros((240, 320, 3), np.uint8)
    out = _overlay().render(blank, FrameResult(visible=False, pose=None), now=0.0)
    assert out.any()


def test_rep_result_flashes_then_expires():
    frames = _frames()
    done = next(f for f in frames if f.rep)
    ov = _overlay()
    blank = np.zeros((480, 640, 3), np.uint8)
    with_flash = ov.render(blank, done, now=100.0)
    quiet = frames[0]
    still = ov.render(blank, quiet, now=101.0)  # 1초 뒤: 아직 표시
    gone = ov.render(blank, quiet, now=110.0)  # 10초 뒤: 사라짐
    right_half = lambda img: img[:, 320:].any()  # noqa: E731
    assert right_half(with_flash) and right_half(still) and not right_half(gone)


# ---- 관절선 색: 올바른 부위는 초록, 잘못된 부위는 점점 빨갛게 -----------------------------


def _px(img, pose, a, b):
    """두 관절을 잇는 선의 한가운데 픽셀(BGR)."""
    mid = (pose.xy[a] + pose.xy[b]) / 2
    return tuple(int(v) for v in img[int(mid[1]), int(mid[0])])


def _canvas(pose):
    h = int(pose.xy[:, 1].max()) + 40
    w = int(pose.xy[:, 0].max()) + 40
    return np.zeros((h, w, 3), np.uint8)


def test_severity_color_runs_green_to_yellow_to_red():
    from aipt_analyzer.overlay import GREEN, severity_color

    assert severity_color(0.0) == GREEN
    mid, red = severity_color(0.5), severity_color(1.0)
    assert mid[1] > 200 and mid[2] > 200  # 노랑: 초록·빨강 성분이 다 있다
    assert red[2] == 255 and red[1] < 60
    reds = [severity_color(s / 10)[2] for s in range(11)]
    assert reds == sorted(reds)  # 값이 커질수록 빨강이 단조롭게 늘어난다 (점점)


def test_bad_trunk_turns_only_the_torso_red_gradually_and_legs_stay_green():
    cfg = load_exercise_config("squat")
    ov = Overlay("squat", load_messages(), "knee_angle", font_path=None, cfg=cfg)
    an = Analyzer(cfg)
    torso_red = []
    for i in range(30):  # 상체를 계속 숙인 채 1초
        t = i / 30
        res = an.process(squat_pose(100, lean=70), t)
        img = ov.render(_canvas(res.pose), res, now=t, text=False)
        torso_red.append(_px(img, res.pose, 5, 11)[2])
        legs = _px(img, res.pose, 11, 13)
    assert torso_red[-1] > 200 and torso_red[-1] >= torso_red[len(torso_red) // 2] >= torso_red[1]
    assert torso_red[1] < torso_red[-1]  # 한 번에 바뀌지 않고 서서히
    assert legs[1] > 200 and legs[2] < 60  # 다리는 초록 그대로
    assert ov._sev["torso"] > 0.9 and ov._sev["legs"] < 0.1


def test_good_form_stays_green_and_color_recovers_slowly():
    cfg = load_exercise_config("squat")
    ov = Overlay("squat", load_messages(), "knee_angle", font_path=None, cfg=cfg)
    an = Analyzer(cfg)
    for i in range(20):
        res = an.process(squat_pose(100, lean=70), i / 30)
        ov.render(_canvas(res.pose), res, now=i / 30, text=False)
    assert ov._sev["torso"] > 0.9
    res = an.process(squat_pose(100, lean=0), 20 / 30)  # 자세를 바로잡는다
    ov.render(_canvas(res.pose), res, now=20 / 30, text=False)
    assert 0.5 < ov._sev["torso"] < 1.0  # 바로 초록이 되지 않고 서서히 회복
    for i in range(21, 90):
        res = an.process(squat_pose(100, lean=0), i / 30)
        img = ov.render(_canvas(res.pose), res, now=i / 30, text=False)
    px = _px(img, res.pose, 5, 11)
    assert px[1] > 200 and px[2] < 60  # 결국 초록


def test_shallow_rep_flashes_the_working_limbs_red():
    cfg = load_exercise_config("squat")
    ov = Overlay("squat", load_messages(), "knee_angle", font_path=None, cfg=cfg)
    an = Analyzer(cfg)
    t = 0.0
    for k in cycle(172, 125):  # 깊이가 모자란 스쿼트 (count는 되지만 target 100+tolerance보다 얕음)
        res = an.process(squat_pose(k), t)
        ov.render(_canvas(res.pose), res, now=t, text=False)
        t += 1 / 30
        if res.rep:
            assert "shallow_depth" in res.rep.feedback
            for j in range(15):
                ov.render(_canvas(res.pose), res, now=t + j / 30, text=False)
            assert ov._sev["legs"] > 0.6 and ov._sev["torso"] < 0.1
            return
    raise AssertionError("rep not completed")


# ---- 새 분석기(보정된 설정) 규칙: direction / live / enabled / full_at / 비율 특징 ----------


def _check(**kw):
    from aipt_analyzer.config import FormCheck

    base = dict(code="c", feature="hand_ratio", stat="mean", limit=2.0, penalty=10)
    return FormCheck(**{**base, **kw})


def test_severity_follows_direction_and_full_at_and_works_on_ratio_features():
    from aipt_analyzer.severity import check_severity

    above = _check(direction="above", limit=2.0, full_at=2.4)
    assert check_severity(above, 1.0) == 0.0  # 멀리 안쪽
    assert check_severity(above, 2.0) == pytest.approx(0.5)  # 기준선
    assert check_severity(above, 2.2) == pytest.approx(0.75)  # full_at까지 서서히
    assert check_severity(above, 2.4) == 1.0 and check_severity(above, 5.0) == 1.0
    below = _check(direction="below", limit=0.5, full_at=0.3)
    assert check_severity(below, 0.9) == 0.0 and check_severity(below, 0.5) == pytest.approx(0.5)
    assert check_severity(below, 0.3) == 1.0
    vals = [check_severity(above, v / 10) for v in range(10, 30)]
    assert vals == sorted(vals)  # 값이 나빠질수록 단조 증가


def test_overlay_ignores_disabled_checks_and_only_colors_live_ones_in_real_time():
    from aipt_analyzer.results import FrameResult
    from tests.helpers import frontal_pose

    cfg = load_exercise_config("sidecrunch")  # hands_behind_head: live. knee_side: 반복 후. gaze_front: 꺼짐
    ov = Overlay("sidecrunch", load_messages(), cfg.primary_feature, font_path=None, cfg=cfg)
    pose = frontal_pose()
    # 팔꿈치 각도(양팔 평균)가 기준(107도)보다 훨씬 큰 = 손이 머리 뒤에 없음 -> 팔이 붉어진다
    bad = FrameResult(visible=True, pose=pose, features={"elbow_angle_both": 150.0, "knee_lat": 0.0, "head_pitch": 0.0},
                      phase="moving", live_feedback=["hands_behind_head"])
    for i in range(30):
        ov.render(_canvas(pose), bad, now=i / 30, text=False)
    assert ov._sev["arms"] > 0.9
    assert ov._sev["legs"] < 0.1  # knee_side는 반복 후 판정이라 실시간으로는 칠하지 않는다


def test_after_rep_checks_flash_their_body_parts():
    from aipt_analyzer.results import FrameResult, RepResult, RepSummary
    from tests.helpers import frontal_pose

    cfg = load_exercise_config("sidecrunch")
    ov = Overlay("sidecrunch", load_messages(), cfg.primary_feature, font_path=None, cfg=cfg)
    pose = frontal_pose()
    summary = RepSummary(1, 0.0, 1.0, "knee_gap", 0.5, {})
    rep = RepResult(summary, 80, ("knee_side",))  # 무릎을 옆으로 안 들었다
    res = FrameResult(visible=True, pose=pose, features={}, phase="up", rep=rep)
    ov.render(_canvas(pose), res, now=0.0, text=False)
    quiet = FrameResult(visible=True, pose=pose, features={}, phase="up")
    for i in range(1, 20):
        ov.render(_canvas(pose), quiet, now=i / 30, text=False)
    assert ov._sev["legs"] > 0.6 and ov._sev["arms"] < 0.1
    for i in range(20, 200):  # 2초 뒤에는 서서히 회복
        ov.render(_canvas(pose), quiet, now=i / 30, text=False)
    assert ov._sev["legs"] < 0.1
