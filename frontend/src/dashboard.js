// 결과 확인 화면: 운동별 통계, 최근 세션 목록, 세션 상세(rep별 점수·피드백)
import { AuthError } from "./api.js";
import { DISPLAY_EXERCISE_LABELS, PLANNED_EXERCISE_LABELS, exerciseLabel, h } from "./dom.js?v=20261110.14";

import { createExercisePicker } from "./exercise-picker.js?v=20261110.14";

const views = new WeakMap();
const fmtDate = (iso) => new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
const clampPct = (n) => Math.max(0, Math.min(100, Number(n) || 0));

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [key, value] of Object.entries({ viewBox: "0 0 24 24", width: "20", height: "20", fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" })) svg.setAttribute(key, value);
  const path = document.createElementNS(svg.namespaceURI, "path");
  path.setAttribute("d", {
    refresh: "M20 7v5h-5 M20 12a8 8 0 1 0-2 5 M20 7v5",
    done: "m5 12 4 4L19 6",
    chevron: "m6 9 6 6 6-6",
    pending: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16 M12 8v4l3 2",
  }[name]);
  svg.append(path);
  return svg;
}

function scoreBar(score) {
  return h("div", { class: "bar" }, h("span", { style: `width:${clampPct(score)}%` }));
}

function summary(stat) {
  return h("div", { class: "history-summary selected-summary" },
    [["총 횟수", `${stat.total_reps}회`], ["운동", `${stat.sessions}번`], ["평균 점수", stat.avg_score == null ? "—" : `${stat.avg_score}점`]].map(([label, value]) =>
      h("div", { class: "summary-item" }, h("h3", {}, label), h("p", { class: "big" }, value))));
}

function sessionDetail(session, messages, label = exerciseLabel) {
  const rows = session.reps.map((r) =>
    h("tr", {},
      h("td", {}, `${r.index}`),
      h("td", {}, scoreBar(r.score), h("span", {}, `${r.score}점`)),
      h("td", {}, `${r.min_angle}°`),
      h("td", {}, r.feedback.length ? r.feedback.map((c) => messages[c] ?? c).join(", ") : "-")));
  return h("div", { class: "detail card" },
    h("h3", {}, `${label(session.exercise)} | ${fmtDate(session.started_at)}`),
    session.reps.length === 0
      ? h("p", { class: "muted" }, "0회")
      : h("div", { class: "table-scroll", tabindex: "0", role: "region", "aria-label": "반복별 결과" }, h("table", {}, h("thead", {}, h("tr", {}, ["#", "점수", "최소 각도", "피드백"].map((t) => h("th", {}, t)))),
          h("tbody", {}, rows))));
}

// ctx: { api, messages, onAuthError }
export async function loadDashboard(root, ctx) {
  views.get(root)?.picker?.destroy();
  const view = { request: 0 };
  views.set(root, view);
  const { api, messages, onAuthError, exerciseLabels = DISPLAY_EXERCISE_LABELS } = ctx;
  const label = key => exerciseLabels[key] ?? exerciseLabel(key);
  root.replaceChildren(h("p", { class: "muted" }, "불러오는 중…"));
  try {
    const [stats, sessions] = await Promise.all([api.stats(), api.sessions(100)]);
    if (views.get(root) !== view) return;
    const choices = [...new Set([...Object.keys(exerciseLabels), ...stats.map(s => s.exercise), ...sessions.map(s => s.exercise)])]
      .map(value => ({ value, label: label(value) }));
    const requested = new URLSearchParams(location.search).get("exercise");
    let selected = choices.some(c => c.value === requested) ? requested : choices[0].value;
    const detail = h("div", {});
    const content = h("div", {});
    const value = h("span", { id: "history-exercise-value" });
    const button = h("button", { id: "history-exercise-select", class: "ghost", type: "button", "aria-label": "기록 종목", "aria-haspopup": "listbox", "aria-controls": "history-exercise-options", "aria-expanded": "false" }, value, icon("chevron"));
    const list = h("div", { id: "history-exercise-options", role: "listbox", "aria-label": "기록 종목", hidden: true });
    const showDetail = async (id) => {
      const request = ++view.request;
      detail.replaceChildren(h("p", { class: "muted" }, "불러오는 중…"));
      try {
        const session = await api.session(id);
        if (views.get(root) !== view || request !== view.request) return;
        detail.replaceChildren(sessionDetail(session, messages, label));
      } catch (e) {
        if (views.get(root) !== view || request !== view.request) return;
        if (e instanceof AuthError) return onAuthError();
        detail.replaceChildren(h("p", { class: "error" }, e.message));
      }
    };
    function render() {
      view.request++;
      detail.replaceChildren();
      if (selected in PLANNED_EXERCISE_LABELS) {
        content.replaceChildren(h("p", { class: "planned-state muted" }, "추가 예정"));
        return;
      }
      const records = sessions.filter(s => s.exercise === selected);
      const stat = stats.find(s => s.exercise === selected) ?? { total_reps: 0, sessions: 0, avg_score: null };
      const table = records.length === 0
        ? h("p", { class: "muted" }, "최근 기록 없음")
        : h("table", { class: "clickable" },
            h("thead", {}, h("tr", {}, ["시작", "횟수", "평균 점수", "상태"].map(t => h("th", {}, t)))),
            h("tbody", {}, records.map(s => h("tr", {
              tabindex: "0", onClick: () => showDetail(s.id), onKeydown: e => e.key === "Enter" && showDetail(s.id),
            }, h("td", {}, fmtDate(s.started_at)), h("td", {}, `${s.total_reps}회`),
              h("td", {}, s.avg_score == null ? "—" : `${s.avg_score}점`),
              h("td", {}, h("span", { class: "status-icon", role: "img", "aria-label": s.status === "finished" ? "완료" : "진행 중", title: s.status === "finished" ? "완료" : "진행 중" }, icon(s.status === "finished" ? "done" : "pending")))))));
      content.replaceChildren(summary(stat),
        h("div", { class: "history-count muted" }, `최근 ${records.length}건`),
        h("div", { class: "table-scroll", tabindex: "0", role: "region", "aria-label": `${label(selected)} 최근 기록` }, table));
    }
    root.replaceChildren(
      h("div", { class: "history-filters" }, h("div", { class: "exercise-picker history-picker" }, button, list),
        h("button", { class: "ghost icon-button", "aria-label": "새로고침", title: "새로고침", onClick: () => loadDashboard(root, ctx) }, icon("refresh"))),
      h("section", { class: "history-surface", "aria-label": "선택 종목 기록" }, content), detail);
    view.picker = createExercisePicker(choices, { button, value, list, selected, onChange: key => {
      selected = key;
      const url = new URL(location.href);
      url.searchParams.set("exercise", key);
      history.replaceState(null, "", url);
      render();
    } });
    render();
  } catch (e) {
    if (views.get(root) !== view) return;
    if (e instanceof AuthError) return onAuthError();
    root.replaceChildren(h("p", { class: "error" }, e.message));
  }
}
