// 리포트·홈·기록 화면용 값 계산. DOM을 건드리지 않는 순수 함수라 노드에서 테스트한다.
import { scoreTier, severityHue } from "./analysis-view.js?v=20261129.1";
import { exerciseLabel } from "./dom.js?v=20261129.1";

export const PART_ORDER = ["arms", "torso", "legs"];
export const PART_LABELS = { arms: "팔", torso: "몸통", legs: "다리" };
export const LEVEL_LABELS = { good: "양호", notice: "주의", weak: "보강 필요" };

const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));
const DAYS = ["일", "월", "화", "수", "목", "금", "토"];

// 부위 약점(평균, 0~1)의 색. 백엔드 등급 경계(0.15 주의, 0.4 보강 필요)가 초록 -> 노랑 -> 빨강의 마디가 되게 늘려 쓴다.
export function weaknessHue(weakness) {
  const w = clamp(weakness ?? 0, 0, 1);
  const sev = w <= 0.15 ? (w / 0.15) * 0.3 : w <= 0.4 ? 0.3 + ((w - 0.15) / 0.25) * 0.4 : 0.7 + clamp((w - 0.4) / 0.4, 0, 1) * 0.3;
  return severityHue(sev);
}

// 백엔드(body_parts.level)와 같은 경계
export const levelOf = (w) => (w >= 0.4 ? "weak" : w >= 0.15 ? "notice" : "good");

const tip = (messages, code) => (code ? (messages[code] ?? code) : null);

// 부위 하나. flagged/total은 리포트(반복 수)에서만 있다.
export function partView(p, messages = {}, total = null) {
  return {
    part: p.part,
    label: PART_LABELS[p.part] ?? p.part,
    weakness: p.weakness,
    level: p.level,
    levelLabel: LEVEL_LABELS[p.level] ?? p.level,
    hue: weaknessHue(p.weakness),
    barPct: p.weakness <= 0 ? 0 : Math.round(clamp(p.weakness, 0.04, 1) * 100),
    detail: total != null ? (p.flagged_reps > 0 ? `${total}회 중 ${p.flagged_reps}회 지적` : "지적 없음") : null,
    tip: tip(messages, p.top_code),
    trend: p.weakness < 0.08 ? null : partTrendView(p.trend), // 거의 지적 없는 부위의 미세한 변화는 말하지 않는다
  };
}

// 약점의 추세. 음수 = 약점이 줄어드는 중 = 좋아지는 중.
export function partTrendView(trend) {
  if (trend == null) return null;
  if (trend <= -0.05) return { tone: "good", text: "좋아지는 중" };
  if (trend >= 0.05) return { tone: "bad", text: "나빠지는 중" };
  return { tone: "flat", text: "비슷해요" };
}

export function gradeText(score) {
  if (score == null) return "";
  return score >= 90 ? "훌륭해요" : score >= 80 ? "좋아요" : score >= 60 ? "조금만 더 다듬어요" : "자세부터 다시 잡아봐요";
}

export function formatDuration(seconds) {
  if (seconds == null) return null;
  const s = Math.max(0, Math.round(seconds));
  return s >= 60 ? `${Math.floor(s / 60)}분 ${s % 60}초` : `${s}초`;
}

export function deltaView(delta) {
  if (delta == null) return null;
  if (delta === 0) return { tone: "flat", text: "이전과 같아요" };
  return { tone: delta > 0 ? "up" : "down", text: `이전보다 ${delta > 0 ? "+" : ""}${delta}점` };
}

// GET /workouts/sessions/{id}/report -> 리포트 화면 값
export function reportView(report, messages = {}) {
  const s = report.session;
  const n = s.reps.length;
  const parts = report.parts.map((p) => partView(p, messages, n));
  const weakest = parts.find((p) => p.part === report.weakest_part) ?? null;
  const focus = report.focus && weakest ? {
    part: weakest.part,
    title: `다음엔 ${weakest.label} 자세를 신경 써요`,
    tip: tip(messages, report.focus.code),
  } : null;
  return {
    empty: n === 0,
    exercise: exerciseLabel(s.exercise),
    exerciseKey: s.exercise,
    startedAt: s.started_at,
    duration: formatDuration(report.duration_seconds),
    score: report.avg_score == null ? null : { value: Math.round(report.avg_score), tier: scoreTier(report.avg_score), grade: gradeText(report.avg_score) },
    delta: deltaView(report.score_delta),
    stats: [
      { label: "횟수", value: `${n}회` },
      { label: "완벽한 반복", value: `${report.perfect_reps}회` },
      { label: "최고 점수", value: n ? `${Math.max(...s.reps.map((r) => r.score))}점` : "—" },
    ],
    parts,
    weakest: weakest?.part ?? null,
    focus,
    verdict: n === 0 ? null : focus ? null : { tone: "good", text: "모든 부위가 안정적이었어요" },
    issues: report.issues.map((i) => ({
      code: i.code,
      text: messages[i.code] ?? i.code,
      count: i.count,
      pct: Math.round(i.rate * 100),
      parts: i.parts.map((p) => PART_LABELS[p] ?? p),
    })),
    timeline: s.reps.map((r) => ({
      index: r.index,
      score: r.score,
      tier: scoreTier(r.score),
      heightPct: clamp(r.score, 6, 100),
      notes: r.feedback.map((c) => messages[c] ?? c),
      parts: PART_ORDER.filter((p) => (r.parts?.[p] ?? 0) > 0).map((p) => PART_LABELS[p]),
    })),
  };
}

const dayLabel = (iso) => DAYS[new Date(`${iso}T00:00:00`).getDay()];

export function recommendationView(rec, messages = {}) {
  if (!rec) return null;
  const name = exerciseLabel(rec.exercise);
  const text = {
    fix: () => `${PART_LABELS[rec.part] ?? ""} 자세가 가장 약해요. ${rec.code ? `"${messages[rec.code] ?? rec.code}" 에 집중해 보세요.` : "집중해서 보강해 봐요."}`,
    variety: () => "약한 부위가 없어요. 한동안 안 한 운동으로 균형을 맞춰 봐요.",
    start: () => "처음이라면 가볍게 시작해 봐요. 카메라 앞에서 몸 전체가 보이게 서면 돼요.",
  }[rec.kind]();
  return {
    exercise: rec.exercise,
    kind: rec.kind,
    title: { fix: `오늘은 ${name}`, variety: `오늘은 ${name}`, start: `${name}로 시작해요` }[rec.kind],
    text,
    part: rec.part,
  };
}

// GET /workouts/insights -> 홈 화면 값
export function homeView(ins, messages = {}) {
  const maxReps = Math.max(1, ...ins.week.map((d) => d.reps));
  const todayIso = ins.week.at(-1)?.date;
  const streak = ins.streak_days;
  return {
    empty: ins.totals.sessions === 0,
    streak,
    todayDone: ins.today_done,
    headline: streak > 0 ? `${streak}일 연속 운동 중` : ins.today_done ? "오늘 운동 완료" : "오늘 운동을 시작해 봐요",
    sub: ins.today_done ? "오늘도 해냈어요. 더 하고 싶다면 추천을 확인해 보세요."
      : streak > 0 ? "오늘 하면 연속 기록이 이어져요." : "하루 한 세트면 충분해요.",
    week: ins.week.map((d) => ({
      date: d.date,
      label: dayLabel(d.date),
      reps: d.reps,
      today: d.date === todayIso,
      done: d.sessions > 0,
      heightPct: d.reps ? Math.max(12, Math.round((d.reps / maxReps) * 100)) : 0,
      tier: d.avg_score == null ? "idle" : scoreTier(d.avg_score),
      title: d.reps ? `${d.date} · ${d.reps}회 · 평균 ${d.avg_score}점` : `${d.date} · 운동 없음`,
    })),
    totals: [
      { label: "누적 횟수", value: `${ins.totals.reps}회` },
      { label: "운동한 날", value: `${ins.totals.active_days}일` },
      { label: "평균 점수", value: ins.totals.avg_score == null ? "—" : `${ins.totals.avg_score}점` },
    ],
    parts: ins.parts.map((p) => partView(p, messages)),
    weakest: ins.weakest_part,
    recommendation: recommendationView(ins.recommendation, messages),
    last: ins.recent.at(-1) ?? null,
  };
}

// 점수 추이 선 그래프: 점수 0~100을 viewBox(width x height) 좌표로. 세션이 하나뿐이면 점만 그린다.
export function scoreTrend(recent, { width = 320, height = 120, pad = 14 } = {}) {
  const pts = recent.filter((r) => r.avg_score != null);
  const n = pts.length;
  const points = pts.map((r, i) => ({
    id: r.id,
    exercise: r.exercise,
    score: r.avg_score,
    tier: scoreTier(r.avg_score),
    x: n === 1 ? width / 2 : pad + (i / (n - 1)) * (width - pad * 2),
    y: pad + (1 - clamp(r.avg_score, 0, 100) / 100) * (height - pad * 2),
    title: `${new Date(r.started_at).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" })} · ${exerciseLabel(r.exercise)} · ${r.avg_score}점 (${r.total_reps}회)`,
  }));
  return {
    width,
    height,
    points,
    line: points.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" "),
    area: n > 1 ? `M${points[0].x.toFixed(1)} ${height - pad} ${points.map((p) => `L${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ")} L${points[n - 1].x.toFixed(1)} ${height - pad} Z` : "",
    guides: [60, 80].map((v) => ({ value: v, y: pad + (1 - v / 100) * (height - pad * 2) })),
    best: n ? Math.max(...pts.map((r) => r.avg_score)) : null,
    latest: n ? pts[n - 1].avg_score : null,
    change: n >= 2 ? Math.round((pts[n - 1].avg_score - pts[0].avg_score) * 10) / 10 : null,
  };
}
