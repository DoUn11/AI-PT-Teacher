// 결과 확인 화면: 운동별 통계, 최근 세션 목록, 세션 상세(rep별 점수·피드백)
import { AuthError } from "./api.js";
import { exerciseLabel, h } from "./dom.js";

const fmtDate = (iso) => new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
const clampPct = (n) => Math.max(0, Math.min(100, Number(n) || 0));

function scoreBar(score) {
  return h("div", { class: "bar" }, h("span", { style: `width:${clampPct(score)}%` }));
}

function statsCards(stats) {
  if (stats.length === 0) return h("p", { class: "muted" }, "아직 종료된 운동 기록이 없습니다.");
  return h("div", { class: "cards" }, stats.map((s) =>
    h("div", { class: "card" },
      h("h3", {}, exerciseLabel(s.exercise)),
      h("p", { class: "big" }, `${s.total_reps}회`),
      h("p", { class: "muted" }, `세션 ${s.sessions}개 · 평균 ${s.avg_score ?? "-"}점`))));
}

function sessionDetail(session, messages) {
  const rows = session.reps.map((r) =>
    h("tr", {},
      h("td", {}, `${r.index}`),
      h("td", {}, scoreBar(r.score), h("span", {}, `${r.score}점`)),
      h("td", {}, `${r.min_angle}°`),
      h("td", {}, r.feedback.length ? r.feedback.map((c) => messages[c] ?? c).join(", ") : "-")));
  return h("div", { class: "detail" },
    h("h3", {}, `${exerciseLabel(session.exercise)} · ${fmtDate(session.started_at)}`),
    session.reps.length === 0
      ? h("p", { class: "muted" }, "기록된 반복이 없습니다.")
      : h("table", {}, h("thead", {}, h("tr", {}, ["#", "점수", "최소 각도", "피드백"].map((t) => h("th", {}, t)))),
          h("tbody", {}, rows)));
}

// ctx: { api, messages, onAuthError }
export async function loadDashboard(root, ctx) {
  const { api, messages, onAuthError } = ctx;
  root.replaceChildren(h("p", { class: "muted" }, "불러오는 중…"));
  try {
    const [stats, sessions] = await Promise.all([api.stats(), api.sessions(20)]);
    const detail = h("div", {});
    const showDetail = async (id) => {
      detail.replaceChildren(h("p", { class: "muted" }, "불러오는 중…"));
      try {
        detail.replaceChildren(sessionDetail(await api.session(id), messages));
      } catch (e) {
        if (e instanceof AuthError) return onAuthError();
        detail.replaceChildren(h("p", { class: "error" }, e.message));
      }
    };

    const table = sessions.length === 0
      ? h("p", { class: "muted" }, "세션이 없습니다. 촬영 탭에서 운동을 시작해 보세요.")
      : h("table", { class: "clickable" },
          h("thead", {}, h("tr", {}, ["시작", "운동", "횟수", "평균 점수", "상태"].map((t) => h("th", {}, t)))),
          h("tbody", {}, sessions.map((s) =>
            h("tr", { tabindex: "0", onClick: () => showDetail(s.id), onKeydown: (e) => e.key === "Enter" && showDetail(s.id) },
              h("td", {}, fmtDate(s.started_at)),
              h("td", {}, exerciseLabel(s.exercise)),
              h("td", {}, `${s.total_reps}`),
              h("td", {}, s.avg_score ?? "-"),
              h("td", {}, s.status === "finished" ? "종료" : "진행 중")))));

    root.replaceChildren(
      h("div", { class: "row" }, h("h2", {}, "운동별 통계"),
        h("button", { class: "ghost", onClick: () => loadDashboard(root, ctx) }, "새로고침")),
      statsCards(stats),
      h("h2", {}, "최근 세션"),
      table,
      detail);
  } catch (e) {
    if (e instanceof AuthError) return onAuthError();
    root.replaceChildren(h("p", { class: "error" }, e.message));
  }
}
