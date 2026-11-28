// 운동 탭: 운동 카드 -> 목표(세트·횟수·강도) 설정 -> 시작하면 영상과 세트 진행 화면으로. 끝나면 리포트로 간다.
import { AuthError } from "./api.js?v=20261129.1";
import { sessionErrorText } from "./analysis-view.js?v=20261129.1";
import { exerciseLabel, h } from "./dom.js?v=20261129.1";
import { mountPiCamera } from "./pi-camera.js?v=20261129.1";
import {
  PACE_HINTS, PACE_LABELS, PACE_ORDER, cardView, changeView, estimate, formatRest, normalizeSetup, needsSave, setupDefaults, volumeText,
} from "./plan-view.js?v=20261129.1";

const NS = "http://www.w3.org/2000/svg";
const POLL_MS = 1500;
const START_WAIT_MS = 8000; // 시작을 눌렀는데 분석기가 이 시간 안에 운동 중으로 바뀌지 않으면 돌아갈 수 있게 한다

// 운동을 그린 간단한 선 그림 (장식)
const FIGURES = {
  pushup: ["M10 30 L28 22 L56 30", "M28 22 L28 38", "M56 30 L60 38"],
  kneeup: ["M32 14 L32 32", "M32 32 L32 48", "M32 32 L44 32 L44 42", "M32 20 L24 28", "M32 20 L40 26"],
  sidecrunch: ["M32 14 Q36 26 32 32", "M32 32 L28 48", "M32 32 L44 34 L44 44", "M33 18 L26 8", "M33 20 L42 14"],
};
const HEADS = { pushup: [8, 27], kneeup: [32, 9], sidecrunch: [28, 8] };

function figure(exercise) {
  const svg = document.createElementNS(NS, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 64 52", class: "wk-figure", "aria-hidden": "true", fill: "none", stroke: "currentColor", "stroke-width": "3", "stroke-linecap": "round", "stroke-linejoin": "round" })) svg.setAttribute(k, v);
  for (const d of FIGURES[exercise] ?? []) {
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  const [cx, cy] = HEADS[exercise] ?? [32, 9];
  const head = document.createElementNS(NS, "circle");
  for (const [k, v] of Object.entries({ cx, cy, r: 4.5 })) head.setAttribute(k, String(v));
  svg.append(head);
  return svg;
}

// −/+ 버튼으로 값을 바꾸는 입력. 값은 get()으로 읽는다.
function stepper({ label, value, min, max, step = 1, format = (v) => String(v), onChange }) {
  let current = value;
  const out = h("output", { class: "stepper-value", "aria-live": "polite" }, format(current));
  const minus = h("button", { type: "button", class: "ghost", "aria-label": `${label} 줄이기` }, "−");
  const plus = h("button", { type: "button", class: "ghost", "aria-label": `${label} 늘리기` }, "+");
  const sync = () => {
    out.textContent = format(current);
    minus.disabled = current <= min;
    plus.disabled = current >= max;
    onChange?.(current);
  };
  minus.addEventListener("click", () => { current = Math.max(min, current - step); sync(); });
  plus.addEventListener("click", () => { current = Math.min(max, current + step); sync(); });
  const el = h("div", { class: "stepper" }, h("span", { class: "stepper-label" }, label), h("div", { class: "stepper-control" }, minus, out, plus));
  sync();
  return { el, get: () => current, set(v) { current = Math.min(max, Math.max(min, v)); sync(); } };
}

// ctx: { api, messages, onAuthError, navigate }
export function mountWorkout(root, ctx) {
  const { api, messages, onAuthError, navigate } = ctx;
  const cardsEl = h("div", { class: "wk-cards" });
  const setupEl = h("div", { class: "wk-setup", hidden: true });
  const liveEl = h("div", { class: "wk-live", hidden: true });
  const liveHost = h("div", {});
  const backBar = h("div", { class: "wk-live-bar" }, h("button", { type: "button", class: "ghost back-button" }, "← 운동 선택"));
  liveEl.append(backBar, liveHost);
  root.replaceChildren(cardsEl, setupEl, liveEl);

  let plans = [];
  let view = "cards";
  let analysis = null;
  let timer = null;
  let running = false;
  let wanted = null;
  let pendingStart = null; // 시작을 누른 시각. 분석기가 따라올 때까지
  let pi = null;
  let setupFor = null;
  let closeSetup = null;

  const authGuard = (e) => {
    if (e instanceof AuthError) { onAuthError(); return true; }
    return false;
  };
  const sessionOf = () => analysis?.info?.session ?? null;
  const analyzerOnline = () => Boolean(analysis?.online);
  const sessionSupported = () => sessionOf() != null;

  // ---- 카드 ---------------------------------------------------------------
  function renderCards() {
    const online = analyzerOnline();
    cardsEl.replaceChildren(
      h("div", { class: "wk-head" },
        h("div", {}, h("h2", {}, "어떤 운동을 할까요?"), h("p", { class: "muted" }, "운동을 고르고 세트와 횟수를 정한 뒤 시작해요. 내 기록에 맞춰 목표를 조금씩 올려 드려요.")),
        h("span", { class: "device-pill", "data-tone": online ? "ok" : "warn" }, online ? "분석기 연결됨" : "분석기 꺼짐")),
      h("div", { class: "wk-grid" }, plans.length === 0 ? [h("p", { class: "muted" }, "불러오는 중…")] : plans.map((p) => card(p))));
  }

  function card(plan) {
    const v = cardView(plan);
    return h("article", { class: "card wk-card", "data-exercise": v.exercise },
      h("div", { class: "wk-card-top" }, figure(v.exercise), h("div", {}, h("h3", {}, v.name), h("p", { class: "muted wk-blurb" }, v.blurb))),
      h("p", { class: "wk-parts muted" }, `자세를 보는 부위 · ${v.parts.join("·")}`),
      h("div", { class: "wk-volume" }, h("b", {}, v.volume), h("span", { class: "muted" }, v.rest)),
      h("div", { class: "wk-tags" },
        h("span", { class: "tag" }, v.auto ? `자동 강도 · ${v.pace}` : "직접 조절"),
        v.isDefault && h("span", { class: "tag", "data-tone": "flat" }, "추천 시작값"),
        v.last && h("span", { class: "tag", "data-tone": v.last.tier }, v.last.text)),
      v.change && h("p", { class: "wk-change", "data-tone": v.change.tone }, v.change.text),
      h("button", { type: "button", onClick: () => openSetup(plan) }, "설정하고 시작"));
  }

  // ---- 설정 ---------------------------------------------------------------
  function openSetup(plan) {
    setupFor = plan.exercise;
    view = "setup";
    const values = setupDefaults(plan);
    const lim = plan.limits;
    const summary = h("p", { class: "wk-summary" });
    const paceHint = h("p", { class: "muted wk-pace-hint" });
    const error = h("p", { class: "error", role: "alert" });
    const paceButtons = new Map(PACE_ORDER.map((key) => [key, h("button", { type: "button", class: "ghost", "aria-pressed": "false", onClick: () => { values.pace = key; refresh(); } }, PACE_LABELS[key])]));
    const auto = h("input", { type: "checkbox", id: "wk-auto" });
    auto.checked = values.auto_progress;
    auto.addEventListener("change", () => { values.auto_progress = auto.checked; refresh(); });
    const sets = stepper({ label: "세트", value: values.sets, min: 1, max: lim.max_sets, format: (v) => `${v}세트`, onChange: (v) => { values.sets = v; refresh(); } });
    const reps = stepper({ label: "한 세트 횟수", value: values.reps, min: 1, max: 50, format: (v) => `${v}회`, onChange: (v) => { values.reps = v; refresh(); } });
    const rest = stepper({ label: "세트 사이 휴식", value: values.rest_seconds, min: 0, max: 300, step: 15, format: formatRest, onChange: (v) => { values.rest_seconds = v; refresh(); } });
    const startButton = h("button", { type: "button", class: "wk-go" }, "시작");
    const freeButton = h("button", { type: "button", class: "ghost" }, "목표 없이 자유롭게");
    const hint = h("div", {});
    function refresh() {
      const e = estimate(values);
      summary.textContent = `총 ${e.total}회 · 약 ${e.minutes}분`;
      for (const [key, b] of paceButtons) b.setAttribute("aria-pressed", String(values.pace === key));
      paceHint.textContent = values.auto_progress ? PACE_HINTS[values.pace] : "목표는 그대로이고, 올려도 될 때 제안만 해요";
    }
    // 내 기록으로 본 지난 평가/제안
    const change = changeView(plan.suggestion ?? plan.last_change);
    if (change) {
      hint.append(h("div", { class: "wk-rec", "data-tone": change.tone },
        h("p", { class: "eyebrow" }, change.suggestOnly ? "추천" : "내 기록 기준"),
        h("p", {}, change.text),
        change.suggestOnly && h("button", { type: "button", class: "ghost", onClick: () => { sets.set(change.after.sets); reps.set(change.after.reps); } }, `${change.target} 적용`)));
    } else if (plan.is_default) {
      hint.append(h("p", { class: "muted wk-rec-note" }, "처음이라 가볍게 시작하는 목표예요. 하다 보면 기록에 맞춰 조정돼요."));
    }
    const online = analyzerOnline();
    startButton.disabled = !online || !sessionSupported();
    const note = !online ? "분석기가 꺼져 있어요. PC에서 ./run_mqtt.sh 를 켜 주세요." : !sessionSupported() ? "분석기를 최신 버전으로 업데이트해 주세요." : "";
    const close = () => { view = "cards"; setupFor = null; setupEl.hidden = true; document.removeEventListener("keydown", onKey); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    closeSetup = close;

    async function go(free) {
      error.textContent = "";
      startButton.disabled = freeButton.disabled = true;
      try {
        const norm = normalizeSetup(values, lim);
        if (!free && needsSave(plan, norm)) await api.savePlan(plan.exercise, norm);
        await api.startWorkout(plan.exercise, free ? null : { sets: norm.sets, reps: norm.reps, rest_seconds: norm.rest_seconds });
      } catch (e) {
        if (authGuard(e)) return;
        error.textContent = sessionErrorText(e);
        startButton.disabled = freeButton.disabled = !online;
        return;
      }
      close();
      enterLive(true);
    }
    startButton.addEventListener("click", () => go(false));
    freeButton.addEventListener("click", () => go(true));
    freeButton.disabled = startButton.disabled;

    setupEl.replaceChildren(h("div", { class: "wk-backdrop", onClick: close }),
      h("section", { class: "card wk-dialog", role: "dialog", "aria-modal": "true", "aria-label": `${exerciseLabel(plan.exercise)} 목표 설정` },
        h("div", { class: "wk-dialog-head" }, figure(plan.exercise), h("div", {}, h("h2", {}, exerciseLabel(plan.exercise)), h("p", { class: "muted" }, `지금 목표 ${volumeText(plan)}`))),
        hint,
        h("div", { class: "wk-steppers" }, sets.el, reps.el, rest.el),
        h("div", { class: "wk-pace" },
          h("div", { class: "wk-pace-head" }, h("span", { class: "stepper-label" }, "강도 올리는 속도"),
            h("label", { class: "inline", for: "wk-auto" }, auto, "자동으로 올리기")),
          h("div", { class: "seg wk-pace-seg", role: "group", "aria-label": "강도 올리는 속도" }, [...paceButtons.values()]),
          paceHint),
        summary, note && h("p", { class: "muted wk-offline" }, note), error,
        h("div", { class: "wk-actions" }, h("button", { type: "button", class: "ghost", onClick: close }, "취소"), freeButton, startButton)));
    setupEl.hidden = false;
    refresh();
    startButton.focus();
  }

  // ---- 운동 화면 ---------------------------------------------------------------
  function enterLive(starting = false) {
    view = "live";
    pendingStart = starting ? Date.now() : null;
    cardsEl.hidden = true;
    setupEl.hidden = true;
    liveEl.hidden = false;
    pi ??= mountPiCamera(liveHost, { api, messages, onAuthError, navigate });
    pi.start();
    updateBackBar();
  }

  function leaveLive() {
    pi?.stop();
    view = "cards";
    pendingStart = null;
    liveEl.hidden = true;
    cardsEl.hidden = false;
    loadPlans(); // 방금 한 운동으로 목표가 바뀌었을 수 있다
  }

  function updateBackBar() {
    const active = sessionOf()?.state === "active";
    if (active) pendingStart = null;
    const waiting = pendingStart != null && Date.now() - pendingStart < START_WAIT_MS;
    backBar.hidden = active || waiting;
  }
  backBar.querySelector("button").addEventListener("click", leaveLive);

  // ---- 갱신 ---------------------------------------------------------------
  async function loadPlans() {
    try {
      plans = await api.plans();
      if (view === "cards") renderCards();
      if (wanted && view === "cards") {
        const plan = plans.find((p) => p.exercise === wanted);
        wanted = null;
        if (plan) openSetup(plan);
      }
    } catch (e) {
      if (authGuard(e)) return;
      cardsEl.replaceChildren(h("p", { class: "error" }, e.message));
    }
  }

  async function poll() {
    if (!running) return;
    try {
      analysis = await api.cameraAnalysis();
      const active = sessionOf()?.state === "active" && analysis.online;
      if (view !== "live" && active) enterLive(false); // 새로고침 등으로 이미 운동 중이면 그 화면으로
      else if (view === "live") updateBackBar();
      else if (view === "cards") {
        const chip = cardsEl.querySelector(".wk-head .device-pill");
        if (chip) { chip.dataset.tone = analysis.online ? "ok" : "warn"; chip.textContent = analysis.online ? "분석기 연결됨" : "분석기 꺼짐"; }
      }
    } catch (e) {
      if (authGuard(e)) return;
    }
    if (running) timer = setTimeout(poll, POLL_MS);
  }

  return {
    preselect(exercise) { wanted = exercise ?? null; },
    start() {
      if (running) return;
      running = true;
      if (view === "live") pi?.start();
      else { view = "cards"; liveEl.hidden = true; cardsEl.hidden = false; setupEl.hidden = true; }
      renderCards();
      loadPlans();
      poll();
    },
    stop() {
      running = false;
      clearTimeout(timer);
      pi?.stop();
      closeSetup?.();
      view = view === "live" ? "live" : "cards";
    },
  };
}
