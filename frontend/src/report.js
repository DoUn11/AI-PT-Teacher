// 세션 리포트: 점수, 부위별 약점(몸 그림), 다음에 신경 쓸 것, 많이 지적된 자세, 반복별 흐름.
import { createBodyMap } from "./body-map.js?v=20261129.1";
import { h } from "./dom.js?v=20261129.1";
import { reportView } from "./insights-view.js?v=20261129.1";

const fmtDate = (iso) => new Date(iso).toLocaleString("ko-KR", { dateStyle: "medium", timeStyle: "short" });

export function scoreRing(value, tier, size = "") {
  const ring = h("div", { class: `score-ring ${size}`.trim(), "data-tone": value == null ? "idle" : tier, role: "img", "aria-label": value == null ? "점수 없음" : `${value}점` },
    h("span", {}, value == null ? "--" : String(value)));
  ring.style.setProperty("--deg", `${value == null ? 0 : Math.round(Math.min(100, value) * 3.6)}deg`);
  return ring;
}

// 부위 줄: 이름, 등급, 막대, 지적 횟수, 가장 많이 지적된 자세. 색은 몸 그림과 같은 hue를 쓴다.
export function partRow(p, { hot = false } = {}) {
  const row = h("li", { class: "part-row", "data-part": p.part, "data-level": p.level, ...(hot ? { "data-hot": "" } : {}) },
    h("div", { class: "part-head" },
      h("b", {}, p.label),
      h("span", { class: "level-pill" }, p.levelLabel),
      p.trend && h("span", { class: "trend-pill", "data-tone": p.trend.tone }, p.trend.text)),
    h("div", { class: "part-bar", "aria-hidden": "true" }, h("span", { style: `width:${p.barPct}%` })),
    p.detail && h("p", { class: "part-detail muted" }, p.detail),
    p.tip && p.level !== "good" && h("p", { class: "part-tip" }, p.tip));
  row.style.setProperty("--h", String(p.hue));
  return row;
}

// report: GET /workouts/sessions/{id}/report. ctx: { messages, onAgain(exercise), onBack() }
export function renderReport(report, { messages = {}, onAgain = null, onBack = null } = {}) {
  const v = reportView(report, messages);
  const root = h("div", { class: "report" });

  const hero = h("section", { class: "card rp-hero" },
    h("div", { class: "rp-title" },
      onBack && h("button", { type: "button", class: "ghost back-button", onClick: onBack }, "← 기록"),
      h("div", {},
        h("h2", {}, v.exercise),
        h("p", { class: "muted" }, [fmtDate(v.startedAt), v.duration].filter(Boolean).join(" · ")))),
    v.score
      ? h("div", { class: "rp-score" },
          scoreRing(v.score.value, v.score.tier, "lg"),
          h("div", {},
            h("p", { class: "rp-grade" }, v.score.grade),
            v.delta && h("span", { class: "delta-pill", "data-tone": v.delta.tone }, v.delta.text),
            h("p", { class: "muted rp-sub" }, "평균 점수")))
      : h("p", { class: "muted" }, "완료한 반복이 없어요"),
    h("dl", { class: "rp-stats" }, v.stats.map((s) => h("div", {}, h("dt", {}, s.label), h("dd", {}, s.value)))));
  root.append(hero);
  if (v.empty) {
    root.append(h("section", { class: "card" }, h("p", { class: "muted" }, "이 세션에는 완료된 반복이 없어서 분석할 내용이 없어요. 몸 전체가 카메라에 보이게 서서 다시 시도해 보세요."), onAgain && h("button", { type: "button", onClick: () => onAgain(v.exerciseKey) }, "다시 하기")));
    return root;
  }

  const rows = new Map();
  const map = createBodyMap({ onHover: (part) => highlight(part) });
  const list = h("ul", { class: "part-list" });
  for (const p of v.parts) {
    const row = partRow(p);
    rows.set(p.part, row);
    row.addEventListener("mouseenter", () => highlight(p.part));
    row.addEventListener("mouseleave", () => highlight(null));
    list.append(row);
  }
  function highlight(part) {
    map.highlight(part);
    for (const [name, row] of rows) row.toggleAttribute("data-hot", name === part);
  }
  map.update(v.parts);
  root.append(h("section", { class: "card rp-body" },
    h("h3", {}, "부위별 자세"),
    h("div", { class: "rp-body-grid" },
      h("div", { class: "bodymap-wrap" }, map.el,
        h("div", { class: "bm-legend muted", "aria-hidden": "true" }, h("span", {}, "좋음"), h("i", { class: "bm-scale" }), h("span", {}, "약함"))),
      list)));

  root.append(v.focus
    ? h("section", { class: "card rp-focus", "data-part": v.focus.part },
        h("p", { class: "eyebrow" }, "다음 운동 포인트"),
        h("h3", {}, v.focus.title),
        v.focus.tip && h("p", { class: "rp-tip" }, v.focus.tip),
        onAgain && h("button", { type: "button", onClick: () => onAgain(v.exerciseKey) }, "이 운동 다시 하기"))
    : h("section", { class: "card rp-focus", "data-tone": "good" },
        h("p", { class: "eyebrow" }, "결과"),
        h("h3", {}, v.verdict.text),
        onAgain && h("button", { type: "button", class: "ghost", onClick: () => onAgain(v.exerciseKey) }, "한 번 더 하기")));

  if (v.issues.length) {
    root.append(h("section", { class: "card rp-issues" },
      h("h3", {}, "자주 지적된 자세"),
      h("ol", { class: "issue-list" }, v.issues.map((i) =>
        h("li", {},
          h("div", { class: "issue-head" }, h("span", {}, i.text), h("b", {}, `${i.count}회`)),
          h("div", { class: "issue-bar", "aria-hidden": "true" }, h("span", { style: `width:${Math.max(4, i.pct)}%` })),
          h("p", { class: "muted" }, `전체의 ${i.pct}% · ${i.parts.join("·") || "부위 정보 없음"}`))))));
  }

  root.append(h("section", { class: "card rp-timeline" },
    h("h3", {}, "반복별 점수"),
    h("div", { class: "timeline", role: "list" }, v.timeline.map((r) =>
      h("div", { class: "tl-col", role: "listitem", "data-tone": r.tier, tabindex: "0",
        title: `${r.index}회 · ${r.score}점${r.notes.length ? ` · ${r.notes.join(", ")}` : " · 지적 없음"}` },
      h("span", { class: "tl-bar" }, h("i", { style: `height:${r.heightPct}%` })),
      h("em", {}, String(r.index))))),
    h("details", { class: "rp-detail" },
      h("summary", {}, "반복별 상세 보기"),
      h("div", { class: "table-scroll", tabindex: "0", role: "region", "aria-label": "반복별 결과" },
        h("table", {},
          h("thead", {}, h("tr", {}, ["#", "점수", "최소 각도", "약했던 부위", "피드백"].map((t) => h("th", {}, t)))),
          h("tbody", {}, report.session.reps.map((r, i) => h("tr", {},
            h("td", {}, String(r.index)), h("td", {}, `${r.score}점`), h("td", {}, `${r.min_angle}°`),
            h("td", {}, v.timeline[i].parts.join(", ") || "-"),
            h("td", {}, v.timeline[i].notes.join(", ") || "-"))))))))); 
  return root;
}
