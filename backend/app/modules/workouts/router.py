from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status, BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.firebase import save_workout_session_to_firebase
from app.db import get_db
from app.modules.auth.deps import get_current_user
from app.modules.auth.models import User, utcnow
from app.modules.workouts import insights, plans, progression
from app.modules.workouts.models import (
    Exercise,
    RepRecord,
    SessionStatus,
    WorkoutSession,
)
from app.modules.workouts.schemas import (
    ExerciseStats,
    InsightsOut,
    PlanIn,
    PlanOut,
    ReportOut,
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
    session = WorkoutSession(user_id=user.id, exercise=body.exercise.value, target_sets=body.target_sets,
                             target_reps=body.target_reps, rest_seconds=body.rest_seconds)
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


def _view(session: WorkoutSession) -> insights.SessionView:
    reps = tuple(
        insights.RepView(r.index, r.score, tuple(r.feedback), r.resolved_parts) for r in session.reps
    )
    return insights.SessionView(session.id, session.exercise, session.started_at, reps)


def _plan_result(session: WorkoutSession) -> dict | None:
    if session.target_sets is None or session.target_reps is None:
        return None
    per = session.target_reps
    groups: dict[int, list] = {}
    for rep in session.reps:
        groups.setdefault((rep.index - 1) // per + 1, []).append(rep)
    done = progression.Result(session.id, session.target_sets, per, len(session.reps), session.avg_score)
    return {
        "sets": session.target_sets, "reps": per, "rest_seconds": session.rest_seconds,
        "completed_sets": done.completed_sets, "completion": round(done.completion, 3),
        "per_set": [
            {"set": k, "reps": len(rs), "avg_score": round(sum(r.score for r in rs) / len(rs), 1)}
            for k, rs in sorted(groups.items()) if k <= session.target_sets
        ],
    }


@router.get("/plans", response_model=list[PlanOut])
def list_plans(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """운동 카드용: 운동마다 지금 목표(세트 x 횟수), 강도 조절 방식, 다음 목표 제안, 지난 운동 결과."""
    return [plans.plan_view(db, user.id, ex) for ex in plans.EXERCISES]


@router.put("/plans/{exercise}", response_model=PlanOut)
def save_plan(exercise: Exercise, body: PlanIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """목표와 강도 조절 방식을 저장한다. 세트·횟수를 바꾸면 그때부터의 기록으로 다음 목표를 계산한다."""
    lim = progression.limits(exercise.value)
    if body.sets > lim.max_sets:
        raise HTTPException(422, f"sets must be <= {lim.max_sets} for {exercise.value}")
    plans.save_plan(db, user.id, exercise.value, sets=body.sets, reps=body.reps, rest_seconds=body.rest_seconds,
                    pace=body.pace, auto_progress=body.auto_progress)
    return plans.plan_view(db, user.id, exercise.value)


@router.get("/sessions/{session_id}/report", response_model=ReportOut)
def session_report(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """세션 리포트: 부위별 약점, 많이 지적된 자세, 이전 세션 대비 점수 변화."""
    session = _get_own_session(db, user, session_id)
    previous = db.scalar(
        select(WorkoutSession)
        .where(
            WorkoutSession.user_id == user.id,
            WorkoutSession.exercise == session.exercise,
            WorkoutSession.status == SessionStatus.FINISHED.value,
            WorkoutSession.id != session.id,
            WorkoutSession.started_at < session.started_at,
        )
        .order_by(WorkoutSession.started_at.desc(), WorkoutSession.id.desc())
        .options(selectinload(WorkoutSession.reps))
    )
    if previous is not None and not previous.reps:
        previous = None
    report = insights.session_report(_view(session), _view(previous) if previous else None)
    duration = (session.finished_at - session.started_at).total_seconds() if session.finished_at else None
    plan = plans.get_plan(db, user.id, session.exercise)
    change = plan.last_change if plan and plan.last_change and plan.last_change.get("session_id") == session.id else None
    return {**report, "session": session, "duration_seconds": duration,
            "plan": _plan_result(session), "next_plan": change}


@router.get("/insights", response_model=InsightsOut)
def get_insights(
    tz_offset: int = Query(0, ge=-840, le=840, description="사용자 시간대: UTC에서 동쪽으로 몇 분 (한국 540)"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """홈/기록 화면용 요약. 날짜 경계(연속 운동일, 주간 막대)는 tz_offset 기준 사용자의 하루로 센다."""
    finished = (WorkoutSession.user_id == user.id, WorkoutSession.status == SessionStatus.FINISHED.value)
    recent_sessions = list(
        db.scalars(
            select(WorkoutSession).where(*finished)
            .order_by(WorkoutSession.started_at.desc(), WorkoutSession.id.desc())
            .limit(60).options(selectinload(WorkoutSession.reps))
        )
    )[::-1]  # 오래된 순
    views = [_view(s) for s in recent_sessions]
    started = db.scalars(select(WorkoutSession.started_at).where(*finished, WorkoutSession.total_reps > 0)).all()
    days = {insights.local_day(t, tz_offset) for t in started}
    today = insights.local_day(utcnow(), tz_offset)
    n_sessions, n_reps, avg = db.execute(
        select(func.count(func.distinct(WorkoutSession.id)), func.count(RepRecord.id), func.avg(RepRecord.score))
        .outerjoin(RepRecord, RepRecord.session_id == WorkoutSession.id)
        .where(*finished)
    ).one()
    parts = insights.part_trends(views)
    return {
        "totals": {
            "sessions": n_sessions, "reps": n_reps,
            "avg_score": round(avg, 1) if avg is not None else None, "active_days": len(days),
        },
        "streak_days": insights.streak(days, today),
        "today_done": today in days,
        "week": insights.week_summary(views, today, tz_offset),
        "parts": parts,
        "weakest_part": insights.weakest(parts),
        "recommendation": insights.recommend(views, parts, today, tz_offset),
        "recent": [
            {
                "id": v.id, "exercise": v.exercise, "started_at": v.started_at, "total_reps": len(v.reps),
                "avg_score": v.avg_score, "parts": insights.mean_parts(v.reps),
            }
            for v in views if v.reps
        ][-30:],
    }


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
                parts=rep.parts,
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
    plans.after_finish(db, session)  # 목표가 있던 세션이면 다음 목표를 평가한다 (자동 조정이 켜져 있으면 바로 반영)

    reps_list_data = [
        {
            "index": rep.index,
            "score": rep.score,
            "min_angle": rep.min_angle,
            "feedback": rep.feedback,
            "parts": rep.resolved_parts,
            "completed_at": rep.completed_at.isoformat() if rep.completed_at else str(utcnow())
        }
        for rep in session.reps
    ]
    background_tasks.add_task(
        save_workout_session_to_firebase,
        session_uuid=session.uuid,
        user_uuid=user.uuid,
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
