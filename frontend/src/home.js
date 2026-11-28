// 홈: 오늘 할 운동 추천, 연속 운동일, 주간 활동, 부위별 약점, 기기 준비 상태.
import { AuthError } from "./api.js?v=20261129.1";
import { createBodyMap } from "./body-map.js?v=20261129.1";
import { h, exerciseLabel } from "./dom.js?v=20261129.1";
import { homeView } from "./insights-view.js?v=20261129.1";
import { partRow, scoreRing } from "./report.js?v=20261129.1";

const views = new WeakMap();

const STEPS = [
  ["카메라 켜기", "Pi 카메라와 분석기(PC)를 켜면 아래 '기기 상태'가 초록으로 바뀌어요."],
  ["운동 고르기", "'운동' 탭에서 오늘 할 운동을 고르고 몸 전체가 보이게 서요."],
  ["시작하고 따라 하기", "화면의 관절선이 빨갛게 변하는 부위를 고치면서 하면 돼요."],
  ["리포트 보기", "끝내면 어느 부위가 약했는지 바로 알려 드려요."],
];

function deviceCard(status) {
  const items = status === null
    ? [["카메라", "off", "확인할 수 없어요"], ["분석기", "off", "확인할 수 없어요"]]
    : [
        ["카메라", status.online ? "ok" : "warn", status.online ? `수신 중 · ${status.fps}fps` : status.enabled ? "신호 없음" : "서버에서 꺼져 있어요"],
        ["분석기", status.analyzed.online ? "ok" : "warn", status.analyzed.online ? "실행 중" : "꺼져 있어요"],
      ];
  const ready = status !== null && status.online && status.analyzed.online;
  return h("section", { class: "card hm-device", "data-ready": String(ready) },
    h("div", { class: "hm-device-head" }, h("h3", {}, "기기 상태"),
      h("span", { class: "device-pill", "data-tone": ready ? "ok" : "warn" }, ready ? "운동 준비 완료" : "준비가 필요해요")),
    h("ul", { class: "device-list" }, items.map(([name, tone, text]) =>
      h("li", { "data-tone": tone }, h("i", { "aria-hidden": "true" }), h("b", {}, name), h("span", { class: "muted" }, text)))));
}

// ctx: { api, messages, onAuthError, navigate(tab, params) }
export async function loadHome(root, ctx) {
  const view = {};
  views.set(root, view);
  const { api, messages, onAuthError, navigate } = ctx;
  root.replaceChildren(h("p", { class: "muted" }, "불러오는 중…"));
  try {
    const [ins, status] = await Promise.all([api.insights(), api.cameraStatus().catch((e) => { if (e instanceof AuthError) throw e; return null; })]);
    if (views.get(root) !== view) return;
    const v = homeView(ins, messages);
    const start = (exercise) => navigate("pi", { exercise });

    const rec = v.recommendation;
    const hero = h("section", { class: "card hm-hero" },
      h("div", { class: "hm-hero-text" },
        h("p", { class: "eyebrow" }, v.todayDone ? "오늘의 기록" : "오늘의 운동"),
        h("h2", {}, v.headline),
        h("p", { class: "muted" }, v.sub)),
      rec && h("div", { class: "hm-rec", "data-kind": rec.kind },
        h("p", { class: "eyebrow" }, "추천"),
        h("h3", {}, rec.title),
        h("p", {}, rec.text),
        h("button", { type: "button", onClick: () => start(rec.exercise) }, `${exerciseLabel(rec.exercise)} 하러 가기`)));

    const week = h("section", { class: "card hm-week" },
      h("h3", {}, "이번 주"),
      h("div", { class: "week-bars", role: "list" }, v.week.map((d) =>
        h("div", { class: "week-col", role: "listitem", "data-tone": d.tier, "data-today": d.today ? "" : null, title: d.title },
          h("span", { class: "week-bar" }, h("i", { style: `height:${d.heightPct}%` })),
          h("em", {}, d.label)))),
      h("dl", { class: "hm-totals" }, v.totals.map((t) => h("div", {}, h("dt", {}, t.label), h("dd", {}, t.value)))));

    const map = createBodyMap();
    map.update(v.empty ? [] : v.parts);
    const body = h("section", { class: "card hm-body" },
      h("h3", {}, "내 약점 부위"),
      v.empty
        ? h("p", { class: "muted" }, "운동을 하면 어느 부위의 자세가 약한지 여기에 색으로 보여 드려요.")
        : h("p", { class: "muted" }, "최근 운동 기준이에요. 빨간 부위일수록 자주 틀렸어요."),
      h("div", { class: "rp-body-grid" },
        h("div", { class: "bodymap-wrap" }, map.el),
        v.empty ? h("div") : h("ul", { class: "part-list" }, v.parts.map((p) => partRow(p, { hot: p.part === v.weakest })))));

    const last = v.last;
    const lastCard = last && h("section", { class: "card hm-last" },
      h("h3", {}, "최근 운동"),
      h("div", { class: "hm-last-body" },
        scoreRing(Math.round(last.avg_score), last.avg_score >= 80 ? "good" : last.avg_score >= 60 ? "warn" : "bad"),
        h("div", {},
          h("p", { class: "hm-last-title" }, exerciseLabel(last.exercise)),
          h("p", { class: "muted" }, `${new Date(last.started_at).toLocaleDateString("ko-KR", { month: "long", day: "numeric" })} · ${last.total_reps}회`)),
        h("button", { type: "button", class: "ghost", onClick: () => navigate("dashboard", { session: last.id }) }, "리포트 보기")));

    const first = v.empty && h("section", { class: "card hm-steps" },
      h("h3", {}, "이렇게 시작해요"),
      h("ol", {}, STEPS.map(([title, text]) => h("li", {}, h("b", {}, title), h("span", { class: "muted" }, text)))));

    root.replaceChildren(hero, h("div", { class: "hm-grid" },
      h("div", { class: "hm-col" }, week, lastCard, deviceCard(status)),
      h("div", { class: "hm-col" }, first, body)));
  } catch (e) {
    if (views.get(root) !== view) return;
    if (e instanceof AuthError) return onAuthError();
    root.replaceChildren(h("p", { class: "error" }, e.message));
  }
}
