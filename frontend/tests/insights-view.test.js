import assert from "node:assert/strict";
import { test } from "node:test";

import { gradeText, homeView, levelOf, partTrendView, partView, recommendationView, reportView, scoreTrend, weaknessHue } from "../src/insights-view.js";

const messages = { shallow_depth: "더 깊이 내려가세요", head_down: "고개를 숙이지 말고 정면을 보세요", elbow_knee_close: "팔꿈치와 무릎을 더 가깝게 모아 보세요" };
const part = (name, weakness, extra = {}) => ({ part: name, weakness, level: levelOf(weakness), flagged_reps: 0, top_code: null, trend: null, ...extra });

test("weakness colors run green -> amber -> red and stay in order", () => {
  const hues = [0, 0.15, 0.4, 0.7, 1].map(weaknessHue);
  assert.equal(hues[0], 130);
  assert.ok(hues.every((x, i) => i === 0 || x < hues[i - 1]), hues.join());
  assert.ok(hues[1] > 70 && hues[1] < 100, "주의 경계는 노란 빛이 도는 초록");
  assert.ok(hues[2] < 55 && hues[2] > 30, "보강 필요 경계는 주황");
  assert.equal(hues[4], 0);
  assert.equal(weaknessHue(null), 130);
});

test("levelOf uses the same boundaries as the backend", () => {
  assert.deepEqual([0, 0.149, 0.15, 0.399, 0.4, 1].map(levelOf), ["good", "good", "notice", "notice", "weak", "weak"]);
});

test("partView shows a label, a tip only when something was flagged, and honest counts", () => {
  const p = partView(part("legs", 0.5, { flagged_reps: 3, top_code: "elbow_knee_close" }), messages, 8);
  assert.equal(p.label, "다리");
  assert.equal(p.levelLabel, "보강 필요");
  assert.equal(p.detail, "8회 중 3회 지적");
  assert.equal(p.tip, "팔꿈치와 무릎을 더 가깝게 모아 보세요");
  assert.equal(p.barPct, 50);
  const ok = partView(part("arms", 0), messages, 8);
  assert.equal(ok.detail, "지적 없음");
  assert.equal(ok.barPct, 0);
  assert.equal(partView(part("arms", 0.01), messages).barPct, 4, "조금이라도 있으면 막대가 보이게");
  assert.equal(partView(part("arms", 0.3)).detail, null, "반복 수를 모르면 횟수를 쓰지 않는다");
  assert.equal(partView(part("torso", 0.02, { trend: 0.3 })).trend, null, "거의 지적 없는 부위의 추세는 말하지 않는다");
  assert.equal(partView(part("arms", 0.3, { trend: -0.3 })).trend.tone, "good");
});

test("part trends: negative means improving", () => {
  assert.deepEqual(partTrendView(-0.2), { tone: "good", text: "좋아지는 중" });
  assert.deepEqual(partTrendView(0.2), { tone: "bad", text: "나빠지는 중" });
  assert.equal(partTrendView(0.01).tone, "flat");
  assert.equal(partTrendView(null), null);
});

const session = (reps) => ({
  id: 3, exercise: "sidecrunch", status: "finished", started_at: "2026-11-29T01:00:00Z", finished_at: null, total_reps: reps.length, avg_score: 80,
  reps: reps.map((r, i) => ({ index: i + 1, min_angle: 100, completed_at: "2026-11-29T01:01:00Z", parts: { arms: 0, torso: 0, legs: 0 }, feedback: [], ...r })),
});
const report = (over = {}) => ({
  session: session([{ score: 60, feedback: ["head_down"], parts: { arms: 0, torso: 0.8, legs: 0 } }, { score: 100 }]),
  duration_seconds: 95, avg_score: 80, best_rep: 2, worst_rep: 1, perfect_reps: 1,
  parts: [part("arms", 0), part("torso", 0.4, { flagged_reps: 1, top_code: "head_down" }), part("legs", 0)],
  weakest_part: "torso", focus: { part: "torso", code: "head_down" },
  issues: [{ code: "head_down", count: 1, rate: 0.5, parts: ["torso"] }],
  previous: { id: 1, started_at: "2026-11-28T01:00:00Z", avg_score: 70 }, score_delta: 10, ...over,
});

test("reportView builds the weak-part story: focus, issues and per-rep timeline", () => {
  const v = reportView(report(), messages);
  assert.equal(v.exercise, "스탠딩 사이드 크런치");
  assert.equal(v.duration, "1분 35초");
  assert.deepEqual(v.score, { value: 80, tier: "good", grade: "좋아요" });
  assert.deepEqual(v.delta, { tone: "up", text: "이전보다 +10점" });
  assert.equal(v.weakest, "torso");
  assert.equal(v.focus.title, "다음엔 몸통 자세를 신경 써요");
  assert.equal(v.focus.tip, "고개를 숙이지 말고 정면을 보세요");
  assert.equal(v.verdict, null);
  assert.deepEqual(v.issues, [{ code: "head_down", text: "고개를 숙이지 말고 정면을 보세요", count: 1, pct: 50, parts: ["몸통"] }]);
  assert.deepEqual(v.timeline.map((t) => [t.index, t.tier, t.parts]), [[1, "warn", ["몸통"]], [2, "good", []]]);
  assert.deepEqual(v.stats.map((s) => s.value), ["2회", "1회", "100점"]);
});

test("reportView for a clean session praises instead of nagging, and handles no reps", () => {
  const clean = reportView(report({ weakest_part: null, focus: null, issues: [], parts: ["arms", "torso", "legs"].map((n) => part(n, 0)) }), messages);
  assert.equal(clean.focus, null);
  assert.equal(clean.verdict.text, "모든 부위가 안정적이었어요");
  const none = reportView(report({ session: session([]), avg_score: null, parts: [], weakest_part: null, focus: null, issues: [], score_delta: null }), messages);
  assert.equal(none.empty, true);
  assert.equal(none.score, null);
  assert.equal(none.verdict, null);
});

test("score grades and a missing delta", () => {
  assert.deepEqual([95, 85, 70, 30].map(gradeText), ["훌륭해요", "좋아요", "조금만 더 다듬어요", "자세부터 다시 잡아봐요"]);
  assert.equal(reportView(report({ score_delta: null }), messages).delta, null);
  assert.deepEqual(reportView(report({ score_delta: -5 }), messages).delta, { tone: "down", text: "이전보다 -5점" });
});

test("recommendations explain themselves from the user's own data", () => {
  const fix = recommendationView({ kind: "fix", exercise: "sidecrunch", part: "arms", code: "elbow_knee_close" }, messages);
  assert.equal(fix.title, "오늘은 스탠딩 사이드 크런치");
  assert.match(fix.text, /팔 자세가 가장 약해요/);
  assert.match(fix.text, /팔꿈치와 무릎을 더 가깝게/);
  assert.match(recommendationView({ kind: "variety", exercise: "kneeup", part: null, code: null }).text, /약한 부위가 없어요/);
  assert.equal(recommendationView({ kind: "start", exercise: "pushup", part: null, code: null }).title, "푸시업로 시작해요");
  assert.equal(recommendationView(null), null);
});

const insights = (over = {}) => ({
  totals: { sessions: 3, reps: 20, avg_score: 78.5, active_days: 2 },
  streak_days: 2, today_done: false,
  week: ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27", "2026-11-28", "2026-11-29"].map((date, i) => ({
    date, sessions: i >= 5 ? 1 : 0, reps: i === 5 ? 10 : i === 6 ? 5 : 0, avg_score: i >= 5 ? 90 : null })),
  parts: [part("arms", 0.5, { trend: -0.2 }), part("torso", 0), part("legs", 0.2)],
  weakest_part: "arms",
  recommendation: { kind: "fix", exercise: "pushup", part: "arms", code: "shallow_depth" },
  recent: [{ id: 7, exercise: "pushup", started_at: "2026-11-28T01:00:00Z", total_reps: 10, avg_score: 90, parts: { arms: 0.5, torso: 0, legs: 0 } }],
  ...over,
});

test("homeView: streak headline, week bars scaled to the busiest day, weak part highlighted", () => {
  const v = homeView(insights(), messages);
  assert.equal(v.headline, "2일 연속 운동 중");
  assert.match(v.sub, /연속 기록이 이어져요/);
  assert.deepEqual(v.week.map((d) => d.label), ["월", "화", "수", "목", "금", "토", "일"]);
  assert.deepEqual(v.week.map((d) => d.heightPct), [0, 0, 0, 0, 0, 100, 50]);
  assert.equal(v.week[6].today, true);
  assert.equal(v.week[0].title.includes("운동 없음"), true);
  assert.equal(v.weakest, "arms");
  assert.equal(v.parts[0].trend.text, "좋아지는 중");
  assert.deepEqual(v.totals.map((t) => t.value), ["20회", "2일", "78.5점"]);
  assert.equal(v.recommendation.exercise, "pushup");
  assert.equal(v.last.id, 7);
  assert.equal(v.empty, false);
});

test("homeView for a new user and for someone who already trained today", () => {
  const fresh = homeView(insights({ totals: { sessions: 0, reps: 0, avg_score: null, active_days: 0 }, streak_days: 0, recent: [], weakest_part: null,
    recommendation: { kind: "start", exercise: "pushup", part: null, code: null } }), messages);
  assert.equal(fresh.empty, true);
  assert.equal(fresh.headline, "오늘 운동을 시작해 봐요");
  assert.equal(fresh.last, null);
  assert.equal(fresh.totals[2].value, "—");
  const done = homeView(insights({ today_done: true }), messages);
  assert.equal(done.todayDone, true);
  assert.equal(homeView(insights({ today_done: true, streak_days: 0 }), messages).headline, "오늘 운동 완료");
});

test("scoreTrend maps scores to chart coordinates and summarizes the change", () => {
  const recent = [60, 75, 90].map((s, i) => ({ id: i, exercise: "pushup", started_at: `2026-11-2${i}T01:00:00Z`, total_reps: 8, avg_score: s }));
  const t = scoreTrend(recent, { width: 100, height: 100, pad: 10 });
  assert.deepEqual(t.points.map((p) => p.x), [10, 50, 90]);
  assert.ok(t.points[2].y < t.points[1].y && t.points[1].y < t.points[0].y, "점수가 오를수록 위로");
  assert.equal(t.points[2].tier, "good");
  assert.equal(t.change, 30);
  assert.equal(t.best, 90);
  assert.equal(t.latest, 90);
  assert.match(t.line, /^M10\.0 /);
  assert.ok(t.area.endsWith("Z"));
  const one = scoreTrend(recent.slice(0, 1), { width: 100, height: 100, pad: 10 });
  assert.equal(one.points[0].x, 50);
  assert.equal(one.change, null);
  assert.equal(one.area, "");
  const none = scoreTrend([]);
  assert.deepEqual([none.points.length, none.best, none.latest], [0, null, null]);
});
