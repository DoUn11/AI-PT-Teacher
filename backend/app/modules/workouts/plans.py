"""운동 목표(플랜) 저장과, 끝난 세션으로 다음 목표를 정하는 일 (규칙은 progression.py)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.workouts import progression
from app.modules.workouts.models import ExercisePlan, SessionStatus, WorkoutSession

EXERCISES = ("pushup", "kneeup", "sidecrunch")  # 웹에서 고를 수 있는 운동


def _finished(user_id: int, exercise: str):
    return select(WorkoutSession).where(
        WorkoutSession.user_id == user_id,
        WorkoutSession.exercise == exercise,
        WorkoutSession.status == SessionStatus.FINISHED.value,
    )


def _result(s: WorkoutSession) -> progression.Result:
    return progression.Result(s.id, s.target_sets, s.target_reps, s.total_reps, s.avg_score)


def get_plan(db: Session, user_id: int, exercise: str) -> ExercisePlan | None:
    return db.scalar(select(ExercisePlan).where(ExercisePlan.user_id == user_id, ExercisePlan.exercise == exercise))


def default_values(db: Session, user_id: int, exercise: str) -> tuple[int, int, int]:
    """저장한 목표가 없을 때의 시작값: 목표 없이 한 기록이 있으면 그 80%, 없으면 운동별 기본값."""
    free = db.scalars(
        _finished(user_id, exercise).where(WorkoutSession.target_sets.is_(None), WorkoutSession.total_reps > 0)
        .order_by(WorkoutSession.started_at.desc()).limit(5)
    ).all()
    return progression.baseline(exercise, [s.total_reps for s in free])


def plan_view(db: Session, user_id: int, exercise: str) -> dict:
    plan = get_plan(db, user_id, exercise)
    lim = progression.limits(exercise)
    if plan is None:
        sets, reps, rest = default_values(db, user_id, exercise)
        base = {"sets": sets, "reps": reps, "rest_seconds": rest, "pace": "normal", "auto_progress": True,
                "suggestion": None, "last_change": None}
    else:
        base = {"sets": plan.sets, "reps": plan.reps, "rest_seconds": plan.rest_seconds, "pace": plan.pace,
                "auto_progress": plan.auto_progress, "suggestion": plan.suggestion, "last_change": plan.last_change}
    last = db.scalar(_finished(user_id, exercise).where(WorkoutSession.total_reps > 0)
                     .order_by(WorkoutSession.started_at.desc(), WorkoutSession.id.desc()))
    return {
        "exercise": exercise, **base, "is_default": plan is None,
        "limits": {"floor": lim.floor, "ceiling": lim.ceiling, "max_sets": lim.max_sets},
        "last_session": None if last is None else {
            "id": last.id, "started_at": last.started_at, "reps_done": last.total_reps,
            "target_total": last.target_sets * last.target_reps if last.target_sets else None, "avg_score": last.avg_score,
        },
    }


def save_plan(db: Session, user_id: int, exercise: str, *, sets: int, reps: int, rest_seconds: int,
              pace: str, auto_progress: bool) -> ExercisePlan:
    """목표를 저장한다. 세트·횟수를 사용자가 직접 바꿨다면 지금까지의 기록은 새 목표와 무관하므로 평가를 처음부터 다시 쌓는다."""
    plan = get_plan(db, user_id, exercise)
    if plan is None:
        plan = ExercisePlan(user_id=user_id, exercise=exercise, sets=sets, reps=reps, rest_seconds=rest_seconds,
                            pace=pace, auto_progress=auto_progress)
        db.add(plan)
        changed = True
    else:
        changed = (plan.sets, plan.reps) != (sets, reps)
        plan.sets, plan.reps, plan.rest_seconds = sets, reps, rest_seconds
        plan.pace, plan.auto_progress = pace, auto_progress
    if changed:
        latest = db.scalar(select(WorkoutSession.id).where(WorkoutSession.user_id == user_id, WorkoutSession.exercise == exercise)
                           .order_by(WorkoutSession.id.desc()))
        plan.pointer_session_id = latest
        plan.suggestion = None
        plan.last_change = None
    db.commit()
    return plan


def after_finish(db: Session, session: WorkoutSession) -> ExercisePlan | None:
    """목표가 있던 세션이 끝나면 다음 목표를 평가한다. 자동 조정이 켜져 있으면 바로 반영, 아니면 제안으로 남긴다."""
    if session.target_sets is None or session.target_reps is None or session.total_reps == 0:
        return None
    plan = get_plan(db, session.user_id, session.exercise)
    if plan is None:  # 목표를 저장하지 않고 시작한 경우: 이번 목표를 시작점으로 삼는다
        plan = ExercisePlan(user_id=session.user_id, exercise=session.exercise, sets=session.target_sets,
                            reps=session.target_reps, rest_seconds=session.rest_seconds or 60, pace="normal", auto_progress=True,
                            pointer_session_id=None)
        db.add(plan)
        db.flush()
    stmt = _finished(session.user_id, session.exercise).where(
        WorkoutSession.target_sets == plan.sets, WorkoutSession.target_reps == plan.reps,
    ).order_by(WorkoutSession.started_at, WorkoutSession.id)
    if plan.pointer_session_id is not None:
        stmt = stmt.where(WorkoutSession.id > plan.pointer_session_id)
    pending = [_result(s) for s in db.scalars(stmt)]
    decision = progression.evaluate(session.exercise, plan.sets, plan.reps, plan.pace, pending)
    if decision is None:
        return plan
    applied = plan.auto_progress and decision.change != "hold"
    change = {
        "session_id": session.id, "change": decision.change, "reason": decision.reason, "need_more": decision.need_more,
        "before": {"sets": plan.sets, "reps": plan.reps}, "after": {"sets": decision.sets, "reps": decision.reps},
        "applied": applied,
    }
    plan.last_change = change
    if applied:
        plan.sets, plan.reps = decision.sets, decision.reps
        plan.pointer_session_id = session.id
        plan.suggestion = None
    else:
        plan.suggestion = change if decision.change != "hold" else None
    db.commit()
    return plan
