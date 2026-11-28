// 분석 정보(JSON) -> 화면에 그릴 값. DOM을 건드리지 않는 순수 함수라 테스트할 수 있다.

// 특징 이름, 단위, 표시 자릿수. 각도(°)뿐 아니라 몸통 길이 대비 비율 같은 단위 없는 값도 있다.
export const FEATURE_META = {
  knee_angle: { label: "무릎 각도", unit: "°" },
  hip_angle: { label: "엉덩이 각도", unit: "°" },
  elbow_angle: { label: "팔꿈치 각도", unit: "°" },
  body_line: { label: "몸 일직선", unit: "°" },
  trunk_lean: { label: "상체 기울기", unit: "°" },
  hip_flex: { label: "고관절 각도", unit: "°" },
  elbow_angle_both: { label: "팔꿈치 각도(양팔)", unit: "°" },
  shoulder_angle_both: { label: "어깨 각도(양팔)", unit: "°" },
  torso_sw: { label: "상체 숙임", unit: "" },
  knee_gap: { label: "무릎 높이", unit: "" },
  knee_lat: { label: "무릎 옆으로", unit: "" },
  ankle_lift: { label: "발목 높이 차", unit: "" },
  head_pitch: { label: "고개 숙임", unit: "" },
  head_yaw: { label: "시선 좌우", unit: "" },
  hand_ratio: { label: "손 간격", unit: "" },
  hand_out: { label: "손 벌어짐", unit: "" },
};

export const featureLabel = (name) => FEATURE_META[name]?.label ?? name;

// 각도는 정수 + °, 비율은 소수 둘째 자리. 모르는 특징은 비율처럼 보여 준다.
export function formatFeature(name, value) {
  if (value == null || Number.isNaN(value)) return "--";
  return FEATURE_META[name]?.unit === "°" ? `${Math.round(value)}°` : value.toFixed(2);
}

// 선택 버튼에는 짧은 이름을 쓴다 (모르는 운동은 코드 그대로)
const SHORT_EXERCISE = { pushup: "푸시업", kneeup: "니업", sidecrunch: "사이드 크런치", squat: "스쿼트" };
export const exerciseShortLabel = (key) => SHORT_EXERCISE[key] ?? key;

// 운동 선택 버튼들의 상태. 선택 표시는 analyzer가 실제로 쓰고 있는 운동(current)을 따른다.
// 바꾸는 중(pending)에는 다른 버튼을 막고, analyzer가 꺼져 있으면(online=false) 전부 막는다.
// locked: 운동 중에는 운동을 바꿀 수 없다 (바꾸려면 먼저 종료)
export function selectorView({ exercises, current, pending, online, locked = false }) {
  return exercises.map((key) => ({
    key,
    label: exerciseShortLabel(key),
    selected: key === current,
    busy: key === pending,
    disabled: !online || pending != null || locked,
  }));
}

// 서버가 돌려준 오류를 사용자 문구로. 409는 analyzer가 꺼져 있을 때, 503은 서버/브로커 쪽 문제.
export function selectErrorText(error) {
  if (error?.status === 409) return "분석기가 꺼져 있어요. PC에서 ./run_mqtt.sh 를 켜 주세요";
  if (error?.status === 503) return "서버에서 운동 선택을 전달하지 못했어요 (MQTT 연결 확인)";
  return error?.message || "운동을 바꾸지 못했어요";
}

// 시작/종료 요청 오류 -> 사용자 문구. 백엔드 detail 문구(영문)로 원인을 가린다.
export function sessionErrorText(error) {
  const msg = String(error?.message ?? "");
  if (error?.status === 409) {
    if (/offline/i.test(msg)) return "분석기가 꺼져 있어요. PC에서 ./run_mqtt.sh 를 켜 주세요";
    if (/already/i.test(msg)) return "이미 운동 중이에요";
    if (/No workout/i.test(msg)) return "진행 중인 운동이 없어요";
    if (/support|update/i.test(msg)) return "분석기를 최신 버전으로 업데이트해 주세요";
  }
  if (error?.status === 503) return "서버에서 전달하지 못했어요 (MQTT 연결 확인)";
  return msg || "요청을 처리하지 못했어요";
}

// info.session -> 화면 상태. 세션을 모르는 예전 분석기는 켜자마자 세션이 열리므로 legacy(조작 없음)로 본다.
export function sessionView(info) {
  const s = info?.session;
  if (!s) return { supported: false, state: "legacy", active: true, last: null };
  const l = s.last;
  return {
    supported: true,
    state: s.state,
    active: s.state === "active",
    last: l && {
      id: l.id,
      saved: l.saved && l.id != null,
      text: `${exerciseShortLabel(l.exercise)} ${l.total_reps}회${l.avg_score == null ? "" : ` · 평균 ${Math.round(l.avg_score)}점`}`,
    },
  };
}

export const PHASES = {
  waiting: { label: "준비", tone: "idle" },
  up: { label: "시작 자세", tone: "idle" },
  moving: { label: "진행 중", tone: "active" },
  down: { label: "최저점", tone: "good" },
};

const GAUGE_OVERSHOOT = 1.25; // 목표 깊이를 넘어 내려간 만큼까지 눈금에 보여 준다
const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));
const TIER_RANK = { strong: 0, medium: 1, weak: 2 };

// 어긋난 정도(0 올바름 ~ 1 어긋남) -> 색상(hue). 영상 관절선과 같은 초록 -> 노랑 -> 빨강이다.
export function severityHue(sev) {
  const s = clamp(sev, 0, 1);
  return Math.round(s <= 0.5 ? 130 - 150 * s : 55 - 110 * (s - 0.5));
}

export const scoreTier = (score) => (score >= 80 ? "good" : score >= 60 ? "warn" : "bad");

export function formatElapsed(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

// 게이지: 0% = 시작 자세(up), 100% = 목표 깊이(target). 내려갈수록 차오른다.
export function gaugeView(info) {
  const { up, count, target } = info.thresholds;
  const span = up - target > 0 ? up - target : 1;
  const value = info.primary.value;
  const progress = value == null ? 0 : (up - value) / span;
  const at = (p) => clamp(p / GAUGE_OVERSHOOT, 0, 1) * 100;
  const reachedCount = value != null && value <= count;
  const reachedTarget = value != null && value <= target;
  const countPct = at((up - count) / span);
  const targetPct = at(1);
  // 보정된 설정은 카운트 기준과 목표가 같을 수 있다. 눈금이 겹쳐 글자가 뭉치지 않게 하나로 합친다.
  const merged = Math.abs(countPct - targetPct) < 4;
  return {
    label: featureLabel(info.primary.feature),
    valueText: formatFeature(info.primary.feature, value),
    fillPct: at(progress),
    tone: reachedTarget ? "good" : reachedCount ? "warn" : "idle",
    markers: [
      { key: "up", label: "시작", pct: at(0) },
      ...(merged ? [{ key: "target", label: "목표·카운트", pct: targetPct }]
        : [{ key: "count", label: "카운트", pct: countPct }, { key: "target", label: "목표", pct: targetPct }]),
    ],
    goodFromPct: targetPct, // 목표 깊이 이상으로 내려간 구간(눈금 오른쪽 끝)을 초록으로 칠하기 위한 시작 위치
  };
}

// 자세 조건 목록: 실시간 조건은 지금 값, 반복 후 조건은 마지막 반복의 값으로 판정한 결과
export function checksView(checks, messages = {}) {
  return checks
    .map((c, i) => ({ c, i }))
    .sort((x, y) => (TIER_RANK[x.c.tier] ?? 3) - (TIER_RANK[y.c.tier] ?? 3) || x.i - y.i) // 정확도 높은 조건 먼저
    .map(({ c }) => ({
      code: c.code,
      text: messages[c.code] ?? c.code,
      state: c.state,
      scope: c.live ? "실시간" : "반복 후",
      valueText: c.value == null ? "—" : formatFeature(c.feature, c.value),
      limitText: `${c.direction === "above" ? "≤" : "≥"} ${formatFeature(c.feature, c.limit)}`, // 이 범위 안이어야 올바름
      hue: c.severity == null ? null : severityHue(c.severity),
    }));
}

export function historyView(scores) {
  return scores.map((score) => ({ score, tier: scoreTier(score), heightPct: clamp(score, 4, 100) }));
}

// data: GET /camera/analysis 응답, messages: 피드백 코드 -> 문구
export function analysisView(data, messages = {}) {
  const info = data?.info;
  if (!info) return { hasInfo: false, online: false };
  const text = (code) => messages[code] ?? code;
  const phase = info.visible ? (PHASES[info.phase] ?? PHASES.waiting) : { label: "사람 없음", tone: "warn" };
  const last = info.last_rep;
  const session = sessionView(info);
  const idle = session.state === "idle";
  return {
    hasInfo: true,
    online: data.online,
    session,
    progress: info.session?.progress ?? null, // 세트 진행 (화면 값은 plan-view.progressView)
    idle,
    // 준비 중에는 횟수를 세지 않으므로 시작을 누르기 전에 무엇을 하면 되는지 알려 준다
    ready: idle ? (info.visible ? { tone: "good", text: "준비됐어요 · 시작을 눌러 주세요" } : { tone: "warn", text: "카메라 앞에 서 주세요" }) : null,
    exercise: info.exercise,
    calibrated: info.calibrated,
    reps: info.reps,
    phase,
    elapsed: idle ? "--:--" : formatElapsed(info.elapsed_seconds),
    gauge: gaugeView(info),
    feedback: info.live_feedback.map((code) => ({ code, text: text(code) })),
    checks: checksView(info.checks ?? [], messages),
    lastRep: last && {
      index: last.index,
      score: last.score,
      tier: scoreTier(last.score),
      ringDeg: Math.round(clamp(last.score, 0, 100) * 3.6),
      feedback: last.feedback.map(text),
      duration: `${last.duration.toFixed(1)}초`,
    },
    history: historyView(info.rep_scores),
  };
}
