"""테스트용 합성 자세. 원하는 관절 각도를 가진 측면 자세를 만든다 (왼쪽 몸만 잘 보임)."""
import math

import numpy as np

from aipt_analyzer.pose import KEYPOINT_INDEX, Pose

FPS = 30.0
_LEFT = ("shoulder", "elbow", "wrist", "hip", "knee", "ankle")


def _pose(points: dict[str, tuple[float, float]]) -> Pose:
    xy = np.zeros((17, 2))
    conf = np.full(17, 0.05)  # 오른쪽 몸·얼굴은 안 보이는 값
    for joint, p in points.items():
        i = KEYPOINT_INDEX[f"left_{joint}"]
        xy[i] = p
        conf[i] = 0.9
    return Pose(xy, conf)


def squat_pose(knee: float, lean: float = 0.0) -> Pose:
    """무릎 각도 knee, 상체 기울기 lean(수직 기준)인 서 있는/앉은 자세."""
    k = math.radians(knee)
    l = math.radians(lean)
    ankle = (100.0, 300.0)
    knee_p = (100.0, 200.0)
    hip = (100.0 + 100 * math.sin(k), 200.0 + 100 * math.cos(k))
    shoulder = (hip[0] + 100 * math.sin(l), hip[1] - 100 * math.cos(l))
    return _pose({"ankle": ankle, "knee": knee_p, "hip": hip, "shoulder": shoulder})


def pushup_pose(elbow: float, hip_offset: float = 0.0) -> Pose:
    """팔꿈치 각도 elbow, 엉덩이가 어깨-발목 직선에서 hip_offset(px)만큼 벗어난 자세."""
    e = math.radians(elbow)
    ankle = (0.0, 300.0)
    shoulder = (400.0, 300.0)
    hip = (200.0, 300.0 + hip_offset)
    elbow_p = (shoulder[0], shoulder[1] + 60)
    wrist = (elbow_p[0] + 60 * math.sin(e), elbow_p[1] - 60 * math.cos(e))
    return _pose(
        {"ankle": ankle, "hip": hip, "shoulder": shoulder, "elbow": elbow_p, "wrist": wrist}
    )


def cycle(top: float, bottom: float, frames: int = 30) -> list[float]:
    """top에서 시작해 bottom까지 내려갔다 top으로 돌아오는 값 (코사인 곡선)."""
    return [
        bottom + (top - bottom) * (1 + math.cos(2 * math.pi * i / frames)) / 2
        for i in range(frames + 1)
    ]


def run(analyzer, poses, start_t: float = 0.0):
    """포즈 목록을 FPS 간격으로 넣고 프레임 결과를 모은다."""
    return [analyzer.process(p, start_t + i / FPS) for i, p in enumerate(poses)]


def frontal_pose(knee_h: float = -0.9, shin: float = 0.9) -> Pose:
    """카메라를 마주 보고 선 자세 (몸통 100px, 어깨 폭 40px, 전부 잘 보임). 왼쪽 무릎을 knee_h(몸통 대비 골반
    위 높이)까지 들고, 왼발목은 무릎 아래 shin(몸통 대비)에 둔다. frontend/tests/helpers.js frontalPose와 같다."""
    knee_y = 200 - knee_h * 100
    pts = {
        "nose": (100, 60), "left_eye": (105, 58), "right_eye": (95, 58), "left_ear": (110, 62), "right_ear": (90, 62),
        "left_shoulder": (120, 100), "right_shoulder": (80, 100), "left_elbow": (130, 150), "right_elbow": (70, 150),
        "left_wrist": (135, 195), "right_wrist": (65, 195), "left_hip": (115, 200), "right_hip": (85, 200),
        "left_knee": (115, knee_y), "right_knee": (85, 290), "left_ankle": (115, knee_y + shin * 100),
        "right_ankle": (85, 380),
    }
    xy = np.array([pts[k] for k in KEYPOINT_INDEX], dtype=float)
    return Pose(xy, np.full(17, 0.9))
