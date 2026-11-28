import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db import get_db
from app.modules.auth.deps import get_current_user
from app.modules.auth.models import User
from app.modules.camera import service
from app.modules.camera.models import CameraEvent
from app.modules.camera.schemas import (
    AnalysisOut,
    CameraEventOut,
    CameraStatus,
    ExerciseRequest,
    ExerciseRequested,
    SessionRequested,
    SessionStart,
)

router = APIRouter(prefix="/camera", tags=["camera"])
Source = Literal["raw", "analyzed"]  # raw: Pi 원본, analyzed: 분석기가 오버레이를 그린 화면
_bearer = HTTPBearer(auto_error=False)
BOUNDARY = "frame"


def _stream_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    token: str | None = Query(None, description="<img>는 헤더를 못 붙이므로 스트림만 쿼리 토큰도 허용"),
    db: Session = Depends(get_db),
) -> User:
    raw = creds.credentials if creds else token
    user_id = decode_access_token(raw) if raw else None
    user = db.get(User, user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or missing credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def mjpeg_parts(
    hub: service.CameraHub,
    is_disconnected: Callable[[], Awaitable[bool]],
    interval: float = 0.04,
) -> AsyncIterator[bytes]:
    """새 프레임이 올 때마다 multipart 조각을 내보낸다. 느린 클라이언트에는 오래된 프레임을 쌓지 않는다."""
    last = -1
    while not await is_disconnected():
        frame, seq = hub.latest()
        if frame is not None and seq != last:
            last = seq
            head = f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n"
            yield head.encode() + frame + b"\r\n"
        await asyncio.sleep(interval)


def _hub_for(source: str) -> service.CameraHub:
    # 모듈 속성을 호출 시점에 읽는다 (테스트에서 교체 가능)
    return service.analyzed_hub if source == "analyzed" else service.hub


@router.get("/status", response_model=CameraStatus)
def camera_status(_: User = Depends(get_current_user)):
    a = service.analyzed_hub.status()
    return {
        **service.hub.status(),
        "analyzed": {k: a[k] for k in ("online", "fps", "last_frame_age_seconds")},
    }


@router.get("/analysis", response_model=AnalysisOut)
def camera_analysis(_: User = Depends(get_current_user)):
    """analyzer가 보낸 최신 분석 정보(횟수, 단계, 각도, 피드백, 마지막 반복 점수). 웹이 영상 아래에 보여 준다."""
    return service.info_hub.snapshot()


@router.post("/exercise", response_model=ExerciseRequested, status_code=status.HTTP_202_ACCEPTED)
def select_exercise(body: ExerciseRequest, _: User = Depends(get_current_user)):
    """웹에서 고른 운동으로 analyzer를 바꾼다. analyzer가 받아 바꾸면 /camera/analysis의 exercise가 따라 바뀐다.

    기기가 하나라서 로그인한 누구나 바꿀 수 있다 (영상을 보는 것과 같은 권한).
    """
    if service.control is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Camera control is not enabled (MQTT_URL)")
    if not service.info_hub.snapshot()["online"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Analyzer is offline")  # 꺼져 있으면 요청이 그냥 사라지므로 미리 알린다
    if not service.control.publish_exercise(body.exercise.value):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Broker is not connected")
    return {"requested": body.exercise.value}


def _session_control(command: dict) -> None:
    """세션 시작/종료 명령을 analyzer에 보낸다. 보낼 수 없는 상황은 이유를 담아 오류로 돌려준다."""
    if service.control is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Camera control is not enabled (MQTT_URL)")
    if not service.control.publish_control(command):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Broker is not connected")


def _current_session() -> str | None:
    snap = service.info_hub.snapshot()
    if not snap["online"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Analyzer is offline")
    session = snap["info"].session
    if session is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Analyzer does not support workout sessions (update it)")
    return session.state


@router.post("/session/start", response_model=SessionRequested, status_code=status.HTTP_202_ACCEPTED)
def start_session(body: SessionStart, _: User = Depends(get_current_user)):
    """운동 세션을 시작한다. analyzer가 백엔드에 세션을 열고 0회부터 센다 (/camera/analysis의 session으로 확인)."""
    if service.control is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Camera control is not enabled (MQTT_URL)")
    if _current_session() == "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "A workout session is already running")
    command = {"action": "start", "exercise": body.exercise.value}
    if body.sets is not None:
        command["plan"] = {"sets": body.sets, "reps": body.reps, "rest_seconds": body.rest_seconds}
    _session_control(command)
    return {"requested": "start", "exercise": body.exercise.value}


@router.post("/session/stop", response_model=SessionRequested, status_code=status.HTTP_202_ACCEPTED)
def stop_session(_: User = Depends(get_current_user)):
    """운동 세션을 끝낸다. analyzer가 남은 반복을 올리고 세션을 닫으면 session.last에 리포트로 갈 id가 실린다."""
    if service.control is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Camera control is not enabled (MQTT_URL)")
    if _current_session() != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "No workout session is running")
    _session_control({"action": "stop"})
    return {"requested": "stop"}


@router.post("/session/skip-rest", response_model=SessionRequested, status_code=status.HTTP_202_ACCEPTED)
def skip_rest(_: User = Depends(get_current_user)):
    """세트 사이 휴식(또는 시작 전 준비 시간)을 건너뛰고 바로 다음 세트를 시작한다."""
    if service.control is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Camera control is not enabled (MQTT_URL)")
    if _current_session() != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "No workout session is running")
    _session_control({"action": "skip_rest"})
    return {"requested": "skip_rest"}


@router.get("/snapshot", responses={200: {"content": {"image/jpeg": {}}}})
def camera_snapshot(source: Source = "raw", _: User = Depends(get_current_user)):
    frame, _seq = _hub_for(source).latest()
    if frame is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No frame received yet")
    return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/stream", responses={200: {"content": {"multipart/x-mixed-replace": {}}}})
async def camera_stream(request: Request, source: Source = "raw", _: User = Depends(_stream_user)):
    return StreamingResponse(
        mjpeg_parts(_hub_for(source), request.is_disconnected),
        media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/events", response_model=list[CameraEventOut])
def camera_events(
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(CameraEvent).order_by(CameraEvent.id.desc()).limit(limit)
    ).all()
