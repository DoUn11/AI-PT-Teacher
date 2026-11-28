from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.modules.workouts.models import Exercise  # 업로드가 받아 주는 운동 목록과 같아야 한다


class StreamStatus(BaseModel):
    online: bool
    fps: float
    last_frame_age_seconds: float | None


class CameraStatus(BaseModel):
    enabled: bool  # 서버에 MQTT_URL이 설정돼 카메라 수신이 켜져 있는가
    broker_connected: bool
    online: bool  # 최근 프레임이 오고 있는가
    fps: float
    last_frame_age_seconds: float | None  # 마지막 프레임이 온 지 몇 초 (아직 없으면 None)
    frames_total: int
    # analyzer가 오버레이를 그려 내보내는 분석 화면. analyzer가 꺼져 있으면 online=False
    analyzed: StreamStatus


class CameraEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event: str
    detail: str | None
    created_at: datetime


class Primary(BaseModel):
    feature: str
    value: float | None


class Thresholds(BaseModel):
    up: float  # 시작 자세로 보는 값
    count: float  # 여기까지 내려가야 1회로 셈
    target: float  # 목표 깊이


class LastRep(BaseModel):
    index: int
    score: int
    feedback: list[str]
    primary_min: float
    duration: float


class CheckInfo(BaseModel):
    """자세 조건 하나의 현재 판정. live=True는 지금 값으로, False는 마지막 반복 결과로 낸 것."""

    code: str  # 피드백 코드 (웹이 문구로 바꿔 보여 줌)
    feature: str
    stat: str
    direction: Literal["above", "below"]  # 이 방향으로 limit을 넘으면 위반
    limit: float
    live: bool
    tier: str | None  # 판정 정확도 등급: strong | medium | weak
    value: float | None
    severity: float | None  # 0(올바름) ~ 1(어긋남). 영상 관절선 색과 같은 기준
    state: Literal["ok", "warn", "bad", "pending"]


class LastSession(BaseModel):
    """방금 끝난 세션. 웹이 이 id로 리포트 화면을 연다 (id가 없으면 백엔드에 저장하지 못한 것)."""

    id: int | None
    exercise: str
    total_reps: int
    avg_score: float | None
    saved: bool


class SessionState(BaseModel):
    state: Literal["idle", "active"]  # idle: 준비 중(횟수를 세지 않음), active: 운동 중
    id: int | None  # 진행 중인 세션의 백엔드 id (업로드를 끈 경우 None)
    started_at: datetime | None
    last: LastSession | None


class AnalysisInfo(BaseModel):
    """analyzer가 MQTT로 보내는 분석 정보. 영상에 글자를 쓰지 않고 웹이 이 값으로 직접 그린다."""

    v: int
    exercise: str
    calibrated: bool
    elapsed_seconds: float
    visible: bool
    phase: str  # waiting | up | moving | down
    reps: int
    primary: Primary
    features: dict[str, float]
    thresholds: Thresholds
    live_feedback: list[str]  # 지금 지적된 피드백 코드 (웹이 문구로 바꿔 보여 줌)
    checks: list[CheckInfo] = []  # 켜져 있는 자세 조건별 판정 (v1 분석기는 보내지 않음)
    last_rep: LastRep | None
    rep_scores: list[int]  # 최근 반복 점수 (오래된 것부터)
    session: SessionState | None = None  # v3부터. 없으면 켜자마자 세션이 열리는 예전 분석기


class AnalysisOut(BaseModel):
    online: bool  # 최근에 분석 정보가 오고 있는가 (analyzer가 켜져 있는가)
    age_seconds: float | None
    info: AnalysisInfo | None


class ExerciseRequest(BaseModel):
    exercise: Exercise


class ExerciseRequested(BaseModel):
    requested: str  # analyzer에 전달한 운동. 실제로 바뀌었는지는 /camera/analysis의 exercise로 확인한다


class SessionStart(BaseModel):
    exercise: Exercise


class SessionRequested(BaseModel):
    requested: Literal["start", "stop"]
    exercise: str | None = None  # 시작할 때만. 실제로 시작됐는지는 /camera/analysis의 session.state로 확인한다
