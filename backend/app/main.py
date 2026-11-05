from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db, models  # noqa: F401  (models import는 테이블 등록용)
from app.core.config import settings
from app.modules.auth.router import router as auth_router
from app.modules.workouts.router import router as workouts_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # MVP 단계: 테이블 자동 생성. 스키마가 안정되면 Alembic 마이그레이션으로 교체한다.
    db.Base.metadata.create_all(db.engine)
    yield


app = FastAPI(title="AI PT쌤 API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(workouts_router, prefix="/api/v1")


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok"}
