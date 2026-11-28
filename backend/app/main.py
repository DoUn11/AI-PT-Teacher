import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db, models  # noqa: F401  (models import는 테이블 등록용)
from app.core.config import settings
from app.modules.auth.router import router as auth_router
from app.modules.camera import service as camera_service
from app.modules.camera.models import CameraEvent
from app.modules.camera.router import router as camera_router
from app.modules.workouts.router import router as workouts_router


def _record_camera_event(event: str, detail: str | None) -> None:
    with db.SessionLocal() as session:
        session.add(CameraEvent(event=event, detail=detail))
        session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # MVP 단계: 테이블 자동 생성. 스키마가 안정되면 Alembic 마이그레이션으로 교체한다.
    db.Base.metadata.create_all(db.engine)
    db.upgrade_schema(db.engine)

    camera = None
    if settings.mqtt_url:
        for h in (camera_service.hub, camera_service.analyzed_hub):
            h.on_event = _record_camera_event
            h.offline_after = settings.camera_offline_after_seconds
        camera_service.info_hub.offline_after = settings.camera_offline_after_seconds
        try:
            camera = camera_service.MqttCameraClient(
                camera_service.hub, settings.mqtt_url, settings.mqtt_frame_topic,
                camera_service.analyzed_hub, settings.mqtt_analyzed_topic,
                camera_service.info_hub, settings.mqtt_analysis_topic, settings.mqtt_control_topic)
            camera.start()
            camera_service.control = camera
        except ImportError:
            logging.getLogger("aipt.camera").error(
                "MQTT_URL이 설정됐지만 paho-mqtt가 없습니다: pip install -e '.[mqtt]'")
    try:
        yield
    finally:
        if camera:
            camera_service.control = None
            camera.stop()


app = FastAPI(title="AI PT쌤 API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(workouts_router, prefix="/api/v1")
app.include_router(camera_router, prefix="/api/v1")


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok"}
