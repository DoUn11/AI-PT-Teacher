// 운동 기록: 점수 추이, 약점 부위, 세션 목록. 세션을 누르면 리포트가 열린다 (?session=ID).
import { AuthError } from "./api.js?v=20261129.1";
import { createBodyMap } from "./body-map.js?v=20261129.1";
import { DISPLAY_EXERCISE_LABELS, PLANNED_EXERCISE_LABELS, exerciseLabel, h } from "./dom.js?v=20261129.1";
import { PART_LABELS, PART_ORDER, levelOf, partView, scoreTrend } from "./insights-view.js?v=20261129.1";
import { partRow, renderReport } from "./report.js?v=20261129.1";

const views = new WeakMap();
const fmtDate = (iso) => new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
const SVG = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  el.append(...kids);
  return el;
}

function trendChart(recent) {
  const t = scoreTrend(recent);
  if (t.points.length === 0) return h("p", { class: "muted" }, "아직 점수 기록이 없어요");
  const chart = svgEl("svg", { class: "trend", viewBox: `0 0 ${t.width} ${t.height}`, role: "img", "aria-label": `최근 ${t.points.length}번의 평균 점수 추이` },
    ...t.guides.map((g) => svgEl("line", { class: "trend-guide", x1: 0, x2: t.width, y1: g.y, y2: g.y })),
    t.area && svgEl("path", { class: "trend-area", d: t.area }),
    t.points.length > 1 && svgEl("path", { class: "trend-line", d: t.line }),
    ...t.points.map((p) => svgEl("circle", { class: "trend-dot", "data-tone": p.tier, cx: p.x, cy: p.y, r: 4.5 }, svgEl("title", {}, p.title))));
  const change = t.change == null ? null : h("span", { class: "delta-pill", "data-tone": t.change > 0 ? "up" : t.change < 0 ? "down" : "flat" },
    t.change === 0 ? "변화 없음" : `처음 대비 ${t.change > 0 ? "+" : ""}${t.change}점`);
  return h("div", { class: "trend-wrap" }, chart,
    h("div", { class: "trend-meta" }, h("span", { class: "muted" }, `최근 ${t.points.length}번`), h("span", {}, `최근 ${Math.round(t.latest)}점`), h("span", { class: "muted" }, `최고 ${Math.round(t.best)}점`), change));
}

// 필터에 맞는 세션들의 부위 평균 -> partView 목록
function filteredParts(recent, messages) {
  const sessions = recent.filter((r) => r.total_reps > 0);
  return PART_ORDER.map((part) => {
    const w = sessions.length ? sessions.reduce((s, r) => s + (r.parts[part] ?? 0), 0) / sessions.length : 0;
    return partView({ part, weakness: Math.round(w * 1000) / 1000, level: levelOf(w) }, messages);
  });
}

function partDots(parts) {
  return h("span", { class: "part-dots", "aria-hidden": "true" }, PART_ORDER.map((p) => {
    const w = parts?.[p] ?? 0;
    const dot = h("i", { "data-part": p, title: PART_LABELS[p] });
    dot.style.setProperty("--h", String(partView({ part: p, weakness: w, level: levelOf(w) }).hue));
    dot.dataset.level = parts ? levelOf(w) : "none";
    return dot;
  }));
}

// ctx: { api, messages, onAuthError, navigate(tab, params), exerciseLabels }
export async function loadDashboard(root, ctx) {
  const view = {};
  views.set(root, view);
  const { api, messages, onAuthError, navigate, exerciseLabels = DISPLAY_EXERCISE_LABELS } = ctx;
  root.replaceChildren(h("p", { class: "muted" }, "불러오는 중…"));
  const params = new URLSearchParams(location.search);
  const sessionId = Number(params.get("session")) || null;
  const fail = (e) => {
    if (views.get(root) !== view) return;
    if (e instanceof AuthError) return onAuthError();
    root.replaceChildren(h("p", { class: "error" }, e.status === 404 ? "기록을 찾을 수 없어요" : e.message));
  };

  if (sessionId) {
    try {
      const report = await api.report(sessionId);
      if (views.get(root) !== view) return;
      root.replaceChildren(renderReport(report, {
        messages,
        onBack: () => navigate("dashboard", { session: null }),
        onAgain: (exercise) => navigate("pi", { exercise }),
        // 자동 조정이 꺼져 있을 때의 제안을 목표로 저장한다 (세트·횟수만 바뀌고 휴식·속도 설정은 그대로)
        onApplyPlan: async (change) => {
          const plan = (await api.plans()).find((p) => p.exercise === report.session.exercise);
          await api.savePlan(plan.exercise, { sets: change.after.sets, reps: change.after.reps, rest_seconds: plan.rest_seconds, pace: plan.pace, auto_progress: plan.auto_progress });
        },
      }));
    } catch (e) { fail(e); }
    return;
  }

  try {
    const [stats, sessions, ins] = await Promise.all([api.stats(), api.sessions(100), api.insights()]);
    if (views.get(root) !== view) return;
    const keys = Object.keys(exerciseLabels).filter((k) => !(k in PLANNED_EXERCISE_LABELS));
    const requested = params.get("exercise");
    let selected = keys.includes(requested) ? requested : "all";
    const partsById = new Map(ins.recent.map((r) => [r.id, r.parts]));
    const content = h("div", { class: "hs-content" });
    const filters = h("div", { class: "seg hs-filter", role: "group", "aria-label": "운동 종류" });

    function render() {
      for (const b of filters.children) b.setAttribute("aria-pressed", String(b.dataset.key === selected));
      const match = (x) => selected === "all" || x.exercise === selected;
      const recent = ins.recent.filter(match);
      const records = sessions.filter((x) => match(x) && !(x.status === "finished" && x.total_reps === 0)); // 반복이 없는 세션은 목록을 어지럽히므로 뺀다
      const mine = stats.filter(match);
      const reps = mine.reduce((s, x) => s + x.total_reps, 0);
      const weighted = mine.reduce((s, x) => s + (x.avg_score ?? 0) * x.total_reps, 0);
      const totals = [["총 횟수", `${reps}회`], ["운동", `${mine.reduce((s, x) => s + x.sessions, 0)}번`], ["평균 점수", reps ? `${Math.round((weighted / reps) * 10) / 10}점` : "—"]];
      const map = createBodyMap();
      const parts = filteredParts(recent, messages);
      map.update(recent.some((r) => r.total_reps > 0) ? parts : []);

      content.replaceChildren(
        h("dl", { class: "hm-totals hs-totals card" }, totals.map(([label, value]) => h("div", {}, h("dt", {}, label), h("dd", {}, value)))),
        h("div", { class: "hs-grid" },
          h("section", { class: "card" }, h("h3", {}, "점수 추이"), trendChart(recent)),
          h("section", { class: "card" }, h("h3", {}, "약점 부위"),
            h("div", { class: "rp-body-grid compact" },
              h("div", { class: "bodymap-wrap" }, map.el),
              h("ul", { class: "part-list" }, parts.map((p) => partRow(p)))))),
        h("section", { class: "card" }, h("h3", {}, `세션 ${records.length}건`),
          records.length === 0
            ? h("p", { class: "muted" }, "기록이 없어요. '운동' 탭에서 첫 운동을 시작해 보세요.")
            : h("ul", { class: "session-list" }, records.map((s) =>
                h("li", {}, h("button", { type: "button", class: "ghost session-row", onClick: () => navigate("dashboard", { session: s.id }) },
                  h("span", { class: "sr-main" }, h("b", {}, exerciseLabel(s.exercise)), h("span", { class: "muted" }, fmtDate(s.started_at))),
                  partDots(partsById.get(s.id)),
                  h("span", { class: "sr-reps" }, s.status === "finished" ? `${s.total_reps}회` : "진행 중"),
                  h("span", { class: "sr-score", "data-tone": s.avg_score == null ? "idle" : s.avg_score >= 80 ? "good" : s.avg_score >= 60 ? "warn" : "bad" }, s.avg_score == null ? "—" : `${s.avg_score}점`)))))));
    }

    for (const key of ["all", ...keys]) {
      filters.append(h("button", { type: "button", class: "ghost", "data-key": key, onClick: () => {
        selected = key;
        const url = new URL(location.href);
        url.searchParams.set("exercise", key);
        history.replaceState(null, "", url);
        render();
      } }, key === "all" ? "전체" : exerciseLabels[key]));
    }
    root.replaceChildren(
      h("div", { class: "hs-top" }, filters,
        h("button", { class: "ghost icon-button", "aria-label": "새로고침", title: "새로고침", onClick: () => loadDashboard(root, ctx) }, "↻")),
      content);
    render();
  } catch (e) { fail(e); }
}
