"""세션 리포트와 홈 인사이트 계산. DB를 모르는 순수 함수라서 단독으로 테스트한다."""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.modules.workouts.body_parts import PARTS, code_parts, level

# 웹에서 고를 수 있는 운동 (추천 후보)
RECOMMENDABLE = ("pushup", "kneeup", "sidecrunch")
SESSION_WINDOW = 10  # 부위별 약점은 최근 이 세션 수로 본다
TREND_MIN_SESSIONS = 4  # 추세는 이 세션 수부터


@dataclass(frozen=True)
class RepView:
    index: int
    score: int
    feedback: tuple[str, ...]
    parts: dict[str, float]


@dataclass(frozen=True)
class SessionView:
    id: int
    exercise: str
    started_at: datetime
    reps: tuple[RepView, ...]

    @property
    def avg_score(self) -> float | None:
        return round(sum(r.score for r in self.reps) / len(self.reps), 1) if self.reps else None


def mean_parts(reps: Sequence[RepView]) -> dict[str, float]:
    if not reps:
        return {p: 0.0 for p in PARTS}
    return {p: round(sum(r.parts.get(p, 0.0) for r in reps) / len(reps), 3) for p in PARTS}


def part_reports(exercise: str, reps: Sequence[RepView]) -> list[dict]:
    """한 세션의 부위별 약점: 평균 약점, 지적된 반복 수, 가장 많이 지적된 코드."""
    weak = mean_parts(reps)
    out = []
    for part in PARTS:
        flagged = [r for r in reps if r.parts.get(part, 0.0) > 0]
        codes = Counter(c for r in flagged for c in r.feedback if part in code_parts(exercise, c))
        out.append({
            "part": part,
            "weakness": weak[part],
            "level": level(weak[part]),
            "flagged_reps": len(flagged),
            "top_code": codes.most_common(1)[0][0] if codes else None,
        })
    return out


def weakest(parts: Sequence[dict]) -> str | None:
    top = max(parts, key=lambda p: p["weakness"], default=None)
    return top["part"] if top and top["level"] != "good" else None


def issue_list(exercise: str, reps: Sequence[RepView]) -> list[dict]:
    counts = Counter(c for r in reps for c in r.feedback)
    total = len(reps) or 1
    return [
        {"code": code, "count": n, "rate": round(n / total, 3), "parts": list(code_parts(exercise, code))}
        for code, n in counts.most_common()
    ]


def session_report(view: SessionView, previous: SessionView | None) -> dict:
    parts = part_reports(view.exercise, view.reps)
    focus_part = weakest(parts)
    focus = None
    if focus_part:
        top = next(p for p in parts if p["part"] == focus_part)["top_code"]
        focus = {"part": focus_part, "code": top}
    scores = [r.score for r in view.reps]
    return {
        "avg_score": view.avg_score,
        "best_rep": max(view.reps, key=lambda r: r.score).index if view.reps else None,
        "worst_rep": min(view.reps, key=lambda r: r.score).index if view.reps else None,
        "perfect_reps": sum(1 for s in scores if s >= 100),
        "parts": parts,
        "weakest_part": focus_part,
        "focus": focus,
        "issues": issue_list(view.exercise, view.reps),
        "previous": None if previous is None or previous.avg_score is None else {
            "id": previous.id, "started_at": previous.started_at, "avg_score": previous.avg_score,
        },
        "score_delta": None if previous is None or previous.avg_score is None or view.avg_score is None
        else round(view.avg_score - previous.avg_score, 1),
    }


def local_day(moment: datetime, tz_offset_minutes: int) -> date:
    return (moment.astimezone(timezone.utc) + timedelta(minutes=tz_offset_minutes)).date()


def streak(days: set[date], today: date) -> int:
    """오늘(없으면 어제)부터 거슬러 올라가며 하루도 안 빠진 날 수. 오늘 아직 안 했다고 끊지 않는다."""
    day = today if today in days else today - timedelta(days=1)
    n = 0
    while day in days:
        n += 1
        day -= timedelta(days=1)
    return n


def week_summary(sessions: Sequence[SessionView], today: date, tz_offset_minutes: int, days: int = 7) -> list[dict]:
    by_day: dict[date, list[SessionView]] = {}
    for s in sessions:
        by_day.setdefault(local_day(s.started_at, tz_offset_minutes), []).append(s)
    out = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        group = by_day.get(d, [])
        reps = [r for s in group for r in s.reps]
        out.append({
            "date": d.isoformat(),
            "sessions": len(group),
            "reps": len(reps),
            "avg_score": round(sum(r.score for r in reps) / len(reps), 1) if reps else None,
        })
    return out


def part_trends(sessions: Sequence[SessionView]) -> list[dict]:
    """최근 세션들의 부위별 평균 약점과 추세. sessions는 오래된 순. trend < 0이면 좋아지는 중."""
    window = [s for s in sessions if s.reps][-SESSION_WINDOW:]
    per_session = [mean_parts(s.reps) for s in window]
    overall = {p: round(sum(m[p] for m in per_session) / len(per_session), 3) if per_session else 0.0 for p in PARTS}
    out = []
    for part in PARTS:
        trend = None
        if len(per_session) >= TREND_MIN_SESSIONS:
            half = len(per_session) // 2
            old, new = per_session[:half], per_session[half:]
            trend = round(sum(m[part] for m in new) / len(new) - sum(m[part] for m in old) / len(old), 3)
        out.append({"part": part, "weakness": overall[part], "level": level(overall[part]), "trend": trend})
    return out


def recommend(sessions: Sequence[SessionView], parts: Sequence[dict], today: date, tz_offset_minutes: int) -> dict | None:
    """다음에 할 운동. 가장 약한 부위가 있으면 그 부위가 가장 약하게 나온 운동을 그 지적 사항에 맞춰 권한다.

    kind: fix(약점 보강) | variety(약점 없음 - 한동안 안 한 운동) | start(기록 없음)
    """
    window = [s for s in sessions if s.reps][-SESSION_WINDOW:]
    if not window:
        return {"kind": "start", "exercise": RECOMMENDABLE[0], "part": None, "code": None}
    part = weakest(parts)
    if part:
        by_ex: dict[str, list[SessionView]] = {}
        for s in window:
            by_ex.setdefault(s.exercise, []).append(s)
        exercise = max(by_ex, key=lambda ex: mean_parts([r for s in by_ex[ex] for r in s.reps])[part])
        reports = part_reports(exercise, [r for s in by_ex[exercise] for r in s.reps])
        code = next(p for p in reports if p["part"] == part)["top_code"]
        return {"kind": "fix", "exercise": exercise, "part": part, "code": code}
    last_done = {ex: max((s.started_at for s in sessions if s.exercise == ex), default=None) for ex in RECOMMENDABLE}
    exercise = min(RECOMMENDABLE, key=lambda ex: (last_done[ex] is not None, last_done[ex] or datetime.min.replace(tzinfo=timezone.utc)))
    return {"kind": "variety", "exercise": exercise, "part": None, "code": None}
