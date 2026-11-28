// 영상 아래 분석 패널. 한 번 만들어 두고 값만 바꿔 그린다 (게이지·점수 링이 부드럽게 움직이도록).
import { h } from "./dom.js?v=20261129.1";
import { analysisView, selectErrorText, selectorView, sessionErrorText } from "./analysis-view.js?v=20261129.1";

const SELECT_TIMEOUT_MS = 6000; // 이 시간 안에 analyzer가 바뀌지 않으면 실패로 본다
const STOP_TIMEOUT_MS = 12000; // 종료는 남은 반복을 올리고 세션을 닫느라 더 걸린다

// exercises: 고를 수 있는 운동 키 목록, onSelect(key): 서버에 선택을 보낸다 (실패하면 던진다)
// onStart(key) / onStop(): 운동 세션 시작·종료 요청 (실패하면 던진다). onReport(id): 내가 종료한 세션의 리포트로 이동
export function createAnalysisPanel(messages = {}, { exercises = [], onSelect = null, onStart = null, onStop = null, onReport = null } = {}) {
  const picker = h("div", { class: "seg ap-picker", role: "group", "aria-label": "운동 선택" });
  const pickerNote = h("p", { class: "ap-picker-note muted", role: "status", "aria-live": "polite", hidden: true });
  const calibrated = h("span", { class: "ap-note muted", hidden: true }, "초기값 기준 · 참고용");
  const repsNum = h("strong", { class: "ap-reps-num", "aria-live": "polite" }, "0");
  const phase = h("span", { class: "ap-phase", "data-tone": "idle" }, h("i", { "aria-hidden": "true" }), h("b", {}, "준비"));
  const elapsed = h("span", { class: "ap-elapsed" }, "00:00");

  const gaugeLabel = h("span", { class: "ap-gauge-label" }, "");
  const gaugeValue = h("span", { class: "ap-gauge-value" }, "--");
  const gaugeFill = h("span", { class: "ap-gauge-fill" });
  const gaugeGood = h("span", { class: "ap-gauge-good", "aria-hidden": "true" });
  const gaugeMarks = h("div", { class: "ap-marks", "aria-hidden": "true" });
  const gauge = h("div", { class: "ap-gauge", "data-tone": "idle", role: "meter", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0" },
    h("div", { class: "ap-gauge-head" }, gaugeLabel, gaugeValue),
    h("div", { class: "ap-track" }, gaugeGood, gaugeFill),
    gaugeMarks);

  const feedback = h("div", { class: "ap-chips ap-status", "aria-live": "polite" });
  const checks = h("ul", { class: "ap-checks" });
  const checksBlock = h("div", { class: "ap-block", hidden: true }, h("h3", {}, "자세 체크"), checks);

  const ring = h("div", { class: "ap-ring", "data-tone": "idle" }, h("span", { class: "ap-ring-num" }, "--"));
  const lastTitle = h("p", { class: "ap-last-title" }, "아직 완료한 반복이 없어요");
  const lastMeta = h("p", { class: "ap-last-meta muted" }, "");
  const lastChips = h("div", { class: "ap-chips" });
  const history = h("div", { class: "ap-history", role: "img", "aria-label": "반복별 점수" });

  const offline = h("div", { class: "ap-offline", role: "status", hidden: true },
    h("strong", {}, "분석기 신호가 없어요"),
    h("span", { class: "muted" }, "PC에서 ./run_mqtt.sh 로 분석기를 켜면 여기에 실시간으로 나타나요"));

  const stateLabel = h("span", { class: "ap-state", "data-state": "idle" }, h("i", { "aria-hidden": "true" }), h("b", {}, "확인 중"));
  const mainButton = h("button", { type: "button", class: "ap-main", "data-mode": "start", disabled: true }, "운동 시작");
  const finishedText = h("span", {});
  const finishedLink = h("button", { type: "button", class: "ghost ap-finished-link" }, "리포트 보기");
  const finished = h("div", { class: "ap-finished", role: "status", hidden: true }, h("i", { "aria-hidden": "true" }), finishedText, finishedLink);

  const el = h("div", { class: "card ap", "data-state": "empty" },
    offline,
    h("div", { class: "ap-head" }, h("h2", {}, "운동"), h("div", { class: "ap-head-meta" }, stateLabel, calibrated)),
    h("div", { class: "ap-session" }, picker, mainButton),
    pickerNote,
    finished,
    h("div", { class: "ap-summary" },
      h("div", { class: "ap-cell ap-cell-reps" }, h("span", { class: "ap-cell-label" }, "횟수"), h("div", { class: "ap-reps" }, repsNum, h("span", { class: "ap-reps-unit" }, "회"))),
      h("div", { class: "ap-cell" }, h("span", { class: "ap-cell-label" }, "자세 단계"), phase),
      h("div", { class: "ap-cell" }, h("span", { class: "ap-cell-label" }, "운동 시간"), elapsed)),
    gauge,
    feedback,
    checksBlock,
    h("div", { class: "ap-block ap-last" },
      h("h3", {}, "마지막 반복"),
      h("div", { class: "ap-last-body" }, ring, h("div", { class: "ap-last-text" }, lastTitle, lastMeta), history),
      lastChips));

  let marksKey = "";

  // ---- 운동 선택 ----
  let current = null; // analyzer가 지금 쓰고 있는 운동
  let online = false;
  let sessionState = "legacy"; // idle(준비) | active(운동 중) | legacy(세션을 모르는 예전 분석기)
  let sessionPending = null; // "start" | "stop": 요청했지만 분석기가 아직 따라오지 않음
  let sessionTimer = null;
  let stopRequested = false; // 내가 종료를 눌렀다 -> 리포트가 나오면 바로 이동
  let seenLastId; // 이미 본 마지막 세션 id (처음 받은 값은 새로 끝난 게 아니다)
  let lastSeenInitialized = false;
  let lastSession = null;
  let pending = null; // 바꿔 달라고 요청했지만 아직 analyzer가 따라오지 않은 운동
  let pendingTimer = null;
  const buttons = new Map(exercises.map((key) => [key, h("button", { type: "button", class: "ghost" })]));
  picker.replaceChildren(...buttons.values());

  function setNote(text) {
    pickerNote.textContent = text;
    pickerNote.hidden = !text;
  }

  function renderPicker() {
    for (const v of selectorView({ exercises, current, pending, online, locked: sessionState === "active" || sessionPending != null })) {
      const b = buttons.get(v.key);
      b.textContent = v.busy ? `${v.label}…` : v.label;
      b.setAttribute("aria-pressed", String(v.selected));
      b.setAttribute("aria-busy", String(v.busy));
      b.disabled = v.disabled;
      b.title = !online ? "분석기가 꺼져 있어요" : sessionState === "active" ? "운동 중에는 바꿀 수 없어요. 먼저 종료해 주세요" : "";
    }
  }

  function endPending() {
    pending = null;
    clearTimeout(pendingTimer);
  }

  for (const [key, button] of buttons) {
    button.addEventListener("click", async () => {
      if (!onSelect || key === current || pending != null) return;
      pending = key;
      setNote("분석기에서 운동을 바꾸는 중…");
      renderPicker();
      try {
        await onSelect(key);
      } catch (e) {
        endPending();
        setNote(selectErrorText(e));
        renderPicker();
        return;
      }
      pendingTimer = setTimeout(() => { // 전달은 됐는데 analyzer가 안 바뀜 (그 사이 꺼졌거나 모르는 운동)
        if (pending === key) {
          endPending();
          setNote("분석기가 응답하지 않아요. 켜져 있는지 확인해 주세요");
          renderPicker();
        }
      }, SELECT_TIMEOUT_MS);
    });
  }
  renderPicker();

  function endSessionPending() {
    sessionPending = null;
    clearTimeout(sessionTimer);
  }

  function renderMain() {
    const supported = sessionState !== "legacy";
    mainButton.hidden = !supported;
    const active = sessionState === "active";
    mainButton.dataset.mode = active ? "stop" : "start";
    mainButton.textContent = sessionPending === "start" ? "시작하는 중…" : sessionPending === "stop" ? "저장하는 중…" : active ? "운동 종료" : "운동 시작";
    mainButton.disabled = !online || sessionPending != null || pending != null;
    mainButton.setAttribute("aria-busy", String(sessionPending != null));
  }

  mainButton.addEventListener("click", async () => {
    if (sessionPending != null) return;
    const starting = sessionState !== "active";
    const action = starting ? onStart : onStop;
    if (!action) return;
    sessionPending = starting ? "start" : "stop";
    stopRequested = !starting;
    setNote(starting ? "" : "운동을 저장하는 중…");
    renderMain();
    renderPicker();
    try {
      await (starting ? action(current) : action());
    } catch (e) {
      endSessionPending();
      stopRequested = false;
      setNote(sessionErrorText(e));
      renderMain();
      renderPicker();
      return;
    }
    sessionTimer = setTimeout(() => { // 전달은 됐는데 분석기가 따라오지 않음 (그 사이 꺼졌거나 오래된 분석기)
      endSessionPending();
      stopRequested = false;
      setNote("분석기가 응답하지 않아요. 켜져 있는지 확인해 주세요");
      renderMain();
      renderPicker();
    }, starting ? SELECT_TIMEOUT_MS : STOP_TIMEOUT_MS);
  });

  finishedLink.addEventListener("click", () => { if (lastSession?.saved) onReport?.(lastSession.id); });

  const rows = new Map(); // 조건 코드 -> 행. 값만 바꿔서 색이 부드럽게 변하게 한다

  function checkRow(code) {
    const row = {
      li: h("li", { class: "ap-check" }),
      text: h("span", { class: "ap-check-text" }),
      scope: h("span", { class: "ap-scope" }),
      value: h("b", { class: "ap-check-value" }),
      limit: h("em", { class: "ap-check-limit" }),
    };
    row.li.append(h("i", { class: "ap-dot", "aria-hidden": "true" }), row.text,
      h("span", { class: "ap-check-meta" }, row.scope, row.value, row.limit));
    rows.set(code, row);
    return row;
  }

  function updateChecks(list) {
    checksBlock.hidden = list.length === 0;
    const keep = new Set();
    const lis = list.map((c) => {
      keep.add(c.code);
      const row = rows.get(c.code) ?? checkRow(c.code);
      row.li.dataset.state = c.state;
      if (c.hue == null) row.li.style.removeProperty("--h"); else row.li.style.setProperty("--h", String(c.hue));
      row.text.textContent = c.text;
      row.scope.textContent = c.scope;
      row.value.textContent = c.valueText;
      row.limit.textContent = `기준 ${c.limitText}`;
      row.li.title = `${c.scope} 판정 · 기준 ${c.limitText}`; // 좁은 화면에서 기준 글자를 숨겨도 마우스를 올리면 볼 수 있다
      return row.li;
    });
    for (const code of [...rows.keys()]) if (!keep.has(code)) rows.delete(code);
    checks.replaceChildren(...lis);
  }

  function update(data) {
    const v = analysisView(data, messages);
    el.dataset.state = !v.hasInfo ? "empty" : v.online ? "live" : "stale";
    el.dataset.session = v.hasInfo ? v.session.state : "legacy";
    offline.hidden = v.hasInfo && v.online;
    online = v.hasInfo && v.online;
    current = v.hasInfo ? v.exercise : null;
    if (pending != null && current === pending) { // analyzer가 실제로 바뀌었다
      endPending();
      setNote("");
    }
    sessionState = v.hasInfo ? v.session.state : "legacy";
    if (sessionPending === "start" && sessionState === "active") { endSessionPending(); setNote(""); }
    if (sessionPending === "stop" && sessionState === "idle") { endSessionPending(); setNote(""); }
    renderMain();
    renderPicker();
    if (!v.hasInfo) {
      stateLabel.dataset.state = "off";
      stateLabel.lastChild.textContent = "분석기 꺼짐";
      return;
    }

    stateLabel.dataset.state = !v.online ? "off" : v.session.active ? "active" : "idle";
    stateLabel.lastChild.textContent = !v.online ? "분석기 꺼짐" : sessionPending === "stop" ? "저장 중" : v.session.active ? (v.session.supported ? "운동 중" : "분석 중") : "준비 중";
    // 방금 끝난 세션: 내가 종료를 눌렀다면 리포트로 바로 가고, 아니면 배너로 알려 준다
    const done = v.session.last;
    if (!lastSeenInitialized) { lastSeenInitialized = true; seenLastId = done?.id ?? null; }
    lastSession = done;
    finished.hidden = !(v.idle && done);
    if (done) {
      finishedText.textContent = done.saved ? `방금 끝난 운동 · ${done.text}` : `${done.text} · 기록을 저장하지 못했어요`;
      finishedLink.hidden = !done.saved;
    }
    if (stopRequested && v.idle && done) {
      stopRequested = false;
      if (done.saved && done.id !== seenLastId) onReport?.(done.id);
    }
    if (done) seenLastId = done.id;

    calibrated.hidden = v.calibrated;
    if (repsNum.textContent !== String(v.reps)) {
      repsNum.textContent = String(v.reps);
      repsNum.classList.remove("bump");
      void repsNum.offsetWidth; // 애니메이션을 다시 시작하려고 리플로우를 일으킨다
      repsNum.classList.add("bump");
    }
    phase.dataset.tone = v.phase.tone;
    phase.querySelector("b").textContent = v.phase.label;
    elapsed.textContent = v.elapsed;

    gauge.dataset.tone = v.gauge.tone;
    gaugeLabel.textContent = v.gauge.label;
    gaugeValue.textContent = v.gauge.valueText;
    gaugeFill.style.width = `${v.gauge.fillPct.toFixed(1)}%`;
    gaugeGood.style.left = `${v.gauge.goodFromPct.toFixed(1)}%`;
    gauge.setAttribute("aria-valuenow", String(Math.round(v.gauge.fillPct)));
    const key = v.gauge.markers.map((m) => `${m.key}:${m.pct.toFixed(1)}`).join("|");
    if (key !== marksKey) { // 기준선은 운동이 바뀔 때만 다시 그린다
      marksKey = key;
      gaugeMarks.replaceChildren(...v.gauge.markers.map((m) =>
        h("span", { class: "ap-mark", "data-key": m.key, style: `left:${m.pct.toFixed(1)}%` }, h("i"), h("em", {}, m.label))));
    }

    feedback.replaceChildren(...(v.ready ? [h("span", { class: "ap-chip", "data-tone": v.ready.tone }, v.ready.text)] : v.feedback.length
      ? v.feedback.map((f) => h("span", { class: "ap-chip", "data-tone": "bad" }, f.text))
      : [h("span", { class: "ap-chip", "data-tone": v.phase.tone === "warn" ? "warn" : "good" }, v.phase.tone === "warn" ? "사람이 보이지 않아요" : "자세 좋아요")]));
    updateChecks(v.checks);

    const last = v.lastRep;
    ring.dataset.tone = last ? last.tier : "idle";
    ring.style.setProperty("--deg", `${last ? last.ringDeg : 0}deg`);
    ring.firstChild.textContent = last ? String(last.score) : "--";
    lastTitle.textContent = last ? `${last.index}회 완료` : "아직 완료한 반복이 없어요";
    lastMeta.textContent = last ? `${last.score}점 · ${last.duration}` : "";
    lastChips.replaceChildren(...(last
      ? (last.feedback.length ? last.feedback.map((t) => h("span", { class: "ap-chip", "data-tone": "warn" }, t)) : [h("span", { class: "ap-chip", "data-tone": "good" }, "좋은 자세예요")])
      : []));
    history.replaceChildren(...v.history.map((b, i) =>
      h("span", { class: "ap-bar", "data-tone": b.tier, style: `height:${b.heightPct}%`, title: `${i + 1}번째 ${b.score}점` })));
    history.hidden = v.history.length === 0;
  }

  return { el, update, stop() { endPending(); endSessionPending(); stopRequested = false; }, select(key) { if (key && exercises.includes(key) && online && sessionState !== "active" && key !== current) buttons.get(key)?.click(); } };
}
