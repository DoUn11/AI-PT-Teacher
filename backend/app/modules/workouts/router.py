from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status, BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.firebase import save_workout_session_to_firebase
from app.db import get_db
from app.modules.auth.deps import get_current_user
from app.modules.auth.models import User, utcnow
from app.modules.workouts.models import (
    Exercise,
    RepRecord,
    SessionStatus,
    WorkoutSession,
)
from app.modules.workouts.schemas import (
    ExerciseStats,
    RepsAccepted,
    RepsIn,
    SessionCreate,
    SessionDetail,
    SessionOut,
)

router = APIRouter(prefix="/workouts", tags=["workouts"])


def _get_own_session(db: Session, user: User, session_id: int) -> WorkoutSession:
    # 남의 세션은 존재 여부도 드러나지 않도록 404로 통일한다.
    session = db.scalar(
        select(WorkoutSession)
        .where(WorkoutSession.id == session_id, WorkoutSession.user_id == user.id)
        .options(selectinload(WorkoutSession.reps))
    )
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return session


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(
    body: SessionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = WorkoutSession(user_id=user.id, exercise=body.exercise.value)
    db.add(session)
    db.commit()
    return session


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    exercise: Exercise | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(WorkoutSession).where(WorkoutSession.user_id == user.id)
    if exercise is not None:
        stmt = stmt.where(WorkoutSession.exercise == exercise.value)
    stmt = stmt.order_by(WorkoutSession.started_at.desc(), WorkoutSession.id.desc())
    return db.scalars(stmt.limit(limit).offset(offset)).all()


@router.get("/sessions/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _get_own_session(db, user, session_id)


@router.post(
    "/sessions/{session_id}/reps",
    response_model=RepsAccepted,
    status_code=status.HTTP_201_CREATED,
)
def add_reps(
    session_id: int,
    body: RepsIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """로컬 분석기가 완료된 반복을 올린다. 같은 index를 다시 보내도 무시(재시도 안전)."""
    session = _get_own_session(db, user, session_id)
    if session.status != SessionStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "Session is already finished")

    existing = {rep.index for rep in session.reps}
    accepted = duplicates = 0
    for rep in body.reps:
        if rep.index in existing:
            duplicates += 1
            continue
        existing.add(rep.index)
        db.add(
            RepRecord(
                session_id=session.id,
                index=rep.index,
                score=rep.score,
                min_angle=rep.min_angle,
                feedback=rep.feedback,
                completed_at=rep.completed_at or utcnow(),
            )
        )
        accepted += 1
    db.commit()
    return RepsAccepted(accepted=accepted, duplicates=duplicates)


@router.post("/sessions/{session_id}/finish", response_model=SessionDetail)
def finish_session(
    session_id: int,
    background_tasks: BackgroundTasks, 
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = _get_own_session(db, user, session_id)
    if session.status == SessionStatus.FINISHED.value:
        return session  # 이미 종료된 세션은 그대로 반환 (재시도 안전)

    scores = [rep.score for rep in session.reps]
    session.status = SessionStatus.FINISHED.value
    session.finished_at = utcnow()
    session.total_reps = len(scores)
    session.avg_score = round(sum(scores) / len(scores), 1) if scores else None
    db.commit()
    db.refresh(session)
    
    reps_list_data = [
        {
            "index": rep.index,
            "score": rep.score,
            "min_angle": rep.min_angle,
            "feedback": rep.feedback,
            "completed_at": rep.completed_at.isoformat() if rep.completed_at else str(utcnow())
        }
        for rep in session.reps
    ]
    background_tasks.add_task(
        save_workout_session_to_firebase,
        session_id=session.id,
        user_id=session.user_id,
        exercise=session.exercise,
        status=session.status,
        started_at=session.started_at.isoformat() if session.started_at else str(utcnow()),
        finished_at=session.finished_at.isoformat() if session.finished_at else None,
        total_reps=session.total_reps,
        avg_score=session.avg_score,
        reps_list=reps_list_data
    )
    
    return session




@router.get("/stats", response_model=list[ExerciseStats])
def exercise_stats(
    since: datetime | None = Query(None, description="이 시각 이후에 시작한 세션만 집계"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """종료된 세션 기준 운동별 누적 통계 (웹 대시보드용)."""
    stmt = (
        select(
            WorkoutSession.exercise,
            func.count(func.distinct(WorkoutSession.id)),
            func.count(RepRecord.id),
            func.avg(RepRecord.score),
        )
        .outerjoin(RepRecord, RepRecord.session_id == WorkoutSession.id)
        .where(
            WorkoutSession.user_id == user.id,
            WorkoutSession.status == SessionStatus.FINISHED.value,
        )
        .group_by(WorkoutSession.exercise)
        .order_by(WorkoutSession.exercise)
    )
    if since is not None:
        stmt = stmt.where(WorkoutSession.started_at >= since)
    return [
        ExerciseStats(
            exercise=exercise,
            sessions=sessions,
            total_reps=reps,
            avg_score=round(avg, 1) if avg is not None else None,
        )
        for exercise, sessions, reps, avg in db.execute(stmt)
    ]
