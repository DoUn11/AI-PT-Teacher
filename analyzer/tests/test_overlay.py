import numpy as np

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
