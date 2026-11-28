// 운동 카드·목표 설정·세트 진행 화면용 값 계산 (DOM 없음, 노드에서 테스트한다).
import { exerciseLabel } from "./dom.js?v=20261129.1";
import { PART_LABELS } from "./insights-view.js?v=20261129.1";
import { scoreTier } from "./analysis-view.js?v=20261129.1";

// 카드에 보여 줄 운동 정보. parts는 그 운동에서 자세를 보는 부위.
export const EXERCISE_META = {
  pushup: { parts: ["arms"], blurb: "팔꿈치를 굽혀 가슴을 내리는 상체 운동" },
  kneeup: { parts: ["legs", "torso"], blurb: "서서 무릎을 골반 높이까지 번갈아 올려요" },
  sidecrunch: { parts: ["arms", "legs", "torso"], blurb: "서서 무릎을 옆으로 올려 팔꿈치에 가깝게" },
};

export const PACE_ORDER = ["slow", "normal", "fast"];
export const PACE_LABELS = { slow: "천천히", normal: "보통", fast: "빠르게" };
export const PACE_HINTS = {
  slow: "자세가 좋은 운동이 3번 이어지면 1회 늘려요",
  normal: "자세가 좋은 운동이 2번 이어지면 1회 늘려요",
  fast: "자세가 좋은 운동이 한 번이면 2회 늘려요",
};

export const formatRest = (seconds) => (seconds <= 0 ? "휴식 없음" : seconds >= 60 && seconds % 60 === 0 ? `휴식 ${seconds / 60}분` : `휴식 ${seconds}초`);
export const volumeText = (p) => `${p.sets}세트 × ${p.reps}회`;
export const totalReps = (p) => p.sets * p.reps;

// 백엔드가 준 평가(PlanChange) -> 사용자 문구. reason 코드는 backend/progression.py와 같다.
export function changeView(change) {
  if (!change) return null;
  const to = `${change.after.sets}세트 × ${change.after.reps}회`;
  const text = {
    ready_up: `목표를 채우고 자세도 좋았어요. 다음 목표는 ${to}`,
    new_set: `한 세트 횟수가 상한에 닿아 세트를 늘렸어요. 다음 목표는 ${to}`,
    wait_more: `자세가 좋아요. ${change.need_more}번 더 잘하면 늘려요`,
    low_quality: "목표는 채웠지만 자세 점수가 낮아 그대로 유지해요. 자세를 먼저 다듬어요",
    incomplete: "목표를 다 채우면 늘려요",
    struggled: `조금 힘들었어요. 무리하지 않게 ${to}로 낮췄어요`,
    at_floor: "지금이 가장 낮은 강도예요. 이 목표를 채우는 데 집중해요",
    at_max: "이 운동의 최대 강도에 도달했어요",
  }[change.reason] ?? "";
  const suggestOnly = change.change !== "hold" && !change.applied;
  return {
    tone: change.change === "up" ? "up" : change.change === "down" ? "down" : "flat",
    text: suggestOnly ? text.replace(/다음 목표는|낮췄어요/, (m) => (m === "낮췄어요" ? "낮추는 걸 추천해요" : "추천 목표는")) : text,
    target: change.change === "hold" ? null : to,
    applied: change.applied,
    suggestOnly,
    after: change.after,
  };
}

// GET /workouts/plans의 한 항목 -> 카드
export function cardView(plan) {
  const meta = EXERCISE_META[plan.exercise] ?? { parts: [], blurb: "" };
  const last = plan.last_session;
  return {
    exercise: plan.exercise,
    name: exerciseLabel(plan.exercise),
    blurb: meta.blurb,
    parts: meta.parts.map((p) => PART_LABELS[p]),
    volume: volumeText(plan),
    rest: formatRest(plan.rest_seconds),
    isDefault: plan.is_default,
    pace: PACE_LABELS[plan.pace],
    auto: plan.auto_progress,
    last: last ? {
      text: `지난번 ${last.reps_done}회${last.avg_score == null ? "" : ` · ${Math.round(last.avg_score)}점`}`,
      tier: last.avg_score == null ? "idle" : scoreTier(last.avg_score),
      date: new Date(last.started_at).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" }),
    } : null,
    change: changeView(plan.suggestion ?? plan.last_change),
  };
}

export const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, Math.round(Number(n) || lo)));

// 설정 화면의 입력값. limits는 백엔드가 준 운동별 한계 (세트 최대, 한 세트 횟수 하한·상한).
export function setupDefaults(plan) {
  return { sets: plan.sets, reps: plan.reps, rest_seconds: plan.rest_seconds, pace: plan.pace, auto_progress: plan.auto_progress };
}

export function normalizeSetup(values, limits) {
  return {
    sets: clamp(values.sets, 1, limits.max_sets),
    reps: clamp(values.reps, 1, 100),
    rest_seconds: clamp(values.rest_seconds, 0, 600),
    pace: PACE_ORDER.includes(values.pace) ? values.pace : "normal",
    auto_progress: Boolean(values.auto_progress),
  };
}

// 시작할 때 목표를 저장해야 하는가: 아직 저장한 적 없거나 값이 달라졌을 때
export function needsSave(plan, values) {
  return plan.is_default || ["sets", "reps", "rest_seconds", "pace", "auto_progress"].some((k) => plan[k] !== values[k]);
}

// 한 줄 요약: "총 30회 · 약 6분" (한 회를 4초, 휴식은 그대로 더해서 대략)
export function estimate(values) {
  const total = values.sets * values.reps;
  const seconds = total * 4 + Math.max(0, values.sets - 1) * values.rest_seconds;
  return { total, minutes: Math.max(1, Math.round(seconds / 60)) };
}

export const formatCountdown = (seconds) => {
  const s = Math.max(0, Math.ceil(seconds ?? 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

// info.session.progress -> 패널의 세트 진행 표시
export function progressView(progress) {
  if (!progress) return null;
  const { sets, reps, set_index: index, set_reps: setReps, status, remaining } = progress;
  const resting = status === "resting";
  const ready = status === "ready";
  return {
    status,
    title: `${index} / ${sets} 세트`,
    repsText: `${setReps} / ${reps}회`,
    repsPct: Math.round((Math.min(setReps, reps) / reps) * 100),
    dots: Array.from({ length: sets }, (_, i) => ({
      index: i + 1,
      state: i + 1 < index || (i + 1 === index && resting) ? "done" : i + 1 === index ? "current" : "todo",
    })),
    countdown: remaining == null ? null : formatCountdown(remaining),
    headline: ready ? "자세를 잡아 주세요" : resting ? "휴식 중" : null,
    next: resting ? `다음: ${index + 1}세트 · ${reps}회` : ready ? `${sets}세트 × ${reps}회를 시작해요` : null,
    skippable: ready || resting,
    last: index === sets && !resting,
  };
}

// 리포트의 목표 결과 (GET /workouts/sessions/{id}/report의 plan)
export function planResultView(plan) {
  if (!plan) return null;
  return {
    title: `${plan.completed_sets} / ${plan.sets} 세트 완료`,
    target: `${volumeText(plan)}${plan.rest_seconds == null ? "" : ` · ${formatRest(plan.rest_seconds)}`}`,
    pct: Math.round(plan.completion * 100),
    complete: plan.completed_sets >= plan.sets,
    perSet: plan.per_set.map((s) => ({
      set: s.set,
      text: `${s.reps} / ${plan.reps}회`,
      score: s.avg_score == null ? null : Math.round(s.avg_score),
      tier: s.avg_score == null ? "idle" : scoreTier(s.avg_score),
      partial: s.reps < plan.reps,
    })),
  };
}
