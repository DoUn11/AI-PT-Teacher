import assert from "node:assert/strict";
import { test } from "node:test";

import { analysisView, checksView, exerciseShortLabel, selectErrorText, selectorView, featureLabel, formatFeature, formatElapsed, gaugeView, scoreTier, severityHue } from "../src/analysis-view.js";

const messages = { knee_low: "무릎을 골반 높이까지 올리세요", spine_bend: "상체를 숙이지 말고 곧게 세우세요", trunk_lean: "상체를 더 세우세요" };

const pushup = {
  v: 2, exercise: "pushup", calibrated: true, elapsed_seconds: 75.4, visible: true, phase: "moving", reps: 3,
  primary: { feature: "elbow_angle", value: 135.2 }, features: { elbow_angle: 135.2, hand_ratio: 1.6 },
  thresholds: { up: 150, count: 140, target: 129.721 }, live_feedback: [], checks: [], last_rep: null, rep_scores: [],
};
const kneeup = {
  ...pushup, exercise: "kneeup", primary: { feature: "knee_gap", value: 0.61 },
  thresholds: { up: 0.68, count: 0.58, target: 0.58 },
  checks: [
    { code: "spine_bend", feature: "torso_sw", stat: "min", direction: "below", limit: 1.313, live: true, tier: "medium", value: 1.2, severity: 0.8, state: "bad" },
    { code: "knee_low", feature: "ankle_lift", stat: "max", direction: "below", limit: 0.678, live: false, tier: "strong", value: null, severity: null, state: "pending" },
  ],
};

test("features show units correctly: degrees get ° and ratios do not", () => {
  assert.equal(formatFeature("elbow_angle", 135.2), "135°");
  assert.equal(formatFeature("knee_gap", 0.6123), "0.61"); // 비율이 "1°" 같은 엉뚱한 값이 되지 않는다
  assert.equal(formatFeature("hand_ratio", null), "--");
  assert.equal(featureLabel("hand_ratio"), "손 간격");
  assert.equal(featureLabel("unknown_feature"), "unknown_feature"); // 새 특징이 생겨도 깨지지 않는다
});

test("gauge fills as the user goes deeper and reports the zone", () => {
  const g = (value) => gaugeView({ ...pushup, primary: { ...pushup.primary, value } });
  assert.equal(g(150).fillPct, 0);
  assert.equal(g(150).tone, "idle");
  assert.equal(g(145).tone, "idle"); // 아직 카운트 기준(140)까지 안 내려옴
  assert.equal(g(140).tone, "warn"); // 카운트 기준에 도달
  assert.equal(g(125).tone, "good"); // 목표 깊이(129.7) 도달
  assert.ok(g(125).fillPct > g(140).fillPct && g(140).fillPct > 0);
  assert.equal(g(null).valueText, "--");
});

test("gauge merges count and target markers when the calibrated config makes them equal", () => {
  const merged = gaugeView(kneeup);
  assert.deepEqual(merged.markers.map((m) => m.key), ["up", "target"]);
  assert.equal(merged.markers[1].label, "목표·카운트");
  assert.equal(merged.valueText, "0.61");
  assert.deepEqual(gaugeView(pushup).markers.map((m) => m.key), ["up", "count", "target"]);
});

test("severityHue runs green -> yellow -> red like the overlay lines", () => {
  assert.equal(severityHue(0), 130);
  assert.ok(severityHue(0.5) > 40 && severityHue(0.5) < 70);
  assert.equal(severityHue(1), 0);
  const hues = [0, 0.2, 0.4, 0.6, 0.8, 1].map(severityHue);
  assert.deepEqual([...hues].sort((a, b) => b - a), hues); // 나빠질수록 빨강 쪽으로 단조
});

test("checks view: strong tiers first, per-rep checks labeled, limits show the allowed side", () => {
  const rows = checksView(kneeup.checks, messages);
  assert.deepEqual(rows.map((r) => r.code), ["knee_low", "spine_bend"]); // strong -> medium
  assert.equal(rows[0].scope, "반복 후");
  assert.equal(rows[0].state, "pending");
  assert.equal(rows[0].valueText, "—");
  assert.equal(rows[0].hue, null);
  assert.equal(rows[1].scope, "실시간");
  assert.equal(rows[1].limitText, "≥ 1.31"); // below 위반 = 기준 이상이어야 올바름
  assert.equal(rows[1].text, messages.spine_bend);
  assert.ok(rows[1].hue < 30); // severity 0.8 -> 붉은 쪽
  assert.equal(checksView([{ ...kneeup.checks[0], direction: "above", feature: "trunk_lean", limit: 55 }])[0].limitText, "≤ 55°");
  assert.equal(checksView([{ ...kneeup.checks[0], code: "brand_new_code" }])[0].text, "brand_new_code"); // 문구 없으면 코드
});

test("analysisView works with old analyzers (no checks) and offline/empty states", () => {
  const old = { ...pushup };
  delete old.checks;
  const v = analysisView({ online: true, info: old }, messages);
  assert.deepEqual(v.checks, []);
  assert.equal(v.reps, 3);
  assert.equal(v.elapsed, "01:15");
  assert.equal(v.phase.label, "진행 중");
  assert.deepEqual(analysisView({ online: false, age_seconds: null, info: null }), { hasInfo: false, online: false });
  assert.equal(analysisView({ online: false, info: kneeup }, messages).online, false); // 분석기가 멈췄어도 마지막 값은 남는다
  assert.equal(analysisView({ online: true, info: { ...pushup, visible: false } }).phase.label, "사람 없음");
});

test("last rep, history and score tiers", () => {
  const v = analysisView({
    online: true,
    info: { ...kneeup, last_rep: { index: 2, score: 70, feedback: ["knee_low"], primary_min: 0.61, duration: 1.84 }, rep_scores: [100, 70] },
  }, messages);
  assert.equal(v.lastRep.tier, "warn");
  assert.equal(v.lastRep.ringDeg, 252);
  assert.deepEqual(v.lastRep.feedback, [messages.knee_low]);
  assert.equal(v.lastRep.duration, "1.8초");
  assert.deepEqual(v.history.map((b) => b.tier), ["good", "warn"]);
  assert.deepEqual([100, 80, 79, 60, 59].map(scoreTier), ["good", "good", "warn", "warn", "bad"]);
  assert.equal(formatElapsed(3599.9), "59:59");
});

test("exercise selector follows what the analyzer is actually running", () => {
  const exercises = ["pushup", "kneeup", "sidecrunch"];
  const idle = selectorView({ exercises, current: "pushup", pending: null, online: true });
  assert.deepEqual(idle.map((b) => b.selected), [true, false, false]);
  assert.ok(idle.every((b) => !b.disabled && !b.busy));
  assert.deepEqual(idle.map((b) => b.label), ["푸시업", "니업", "사이드 크런치"]);

  // 바꾸는 중: 선택 표시는 아직 analyzer 기준(pushup)이고, 요청한 버튼만 busy, 전부 눌리지 않게 막는다
  const busy = selectorView({ exercises, current: "pushup", pending: "kneeup", online: true });
  assert.deepEqual(busy.map((b) => b.selected), [true, false, false]);
  assert.deepEqual(busy.map((b) => b.busy), [false, true, false]);
  assert.ok(busy.every((b) => b.disabled));

  // analyzer가 꺼져 있으면 아무것도 고를 수 없다 (요청이 사라지는 것을 막는다)
  assert.ok(selectorView({ exercises, current: null, pending: null, online: false }).every((b) => b.disabled && !b.selected));
  assert.equal(exerciseShortLabel("brand_new"), "brand_new");
});

test("selection errors are explained in plain language", () => {
  assert.match(selectErrorText({ status: 409 }), /꺼져/);
  assert.match(selectErrorText({ status: 503 }), /MQTT/);
  assert.equal(selectErrorText({ status: 500, message: "boom" }), "boom");
  assert.match(selectErrorText(null), /바꾸지 못했어요/);
});

// ---- 운동 세션 (시작/종료) ----------------------------------------------------

import { selectorView as _selectorView, sessionErrorText, sessionView } from "../src/analysis-view.js";

test("session view: old analyzers have no session controls, new ones report idle/active and the last session", () => {
  assert.deepEqual(sessionView({ session: undefined }), { supported: false, state: "legacy", active: true, last: null });
  assert.equal(sessionView({ session: null }).supported, false);
  const idle = sessionView({ session: { state: "idle", id: null, started_at: null, last: { id: 4, exercise: "kneeup", total_reps: 8, avg_score: 82.4, saved: true } } });
  assert.equal(idle.state, "idle");
  assert.equal(idle.active, false);
  assert.deepEqual(idle.last, { id: 4, saved: true, text: "니업 8회 · 평균 82점" });
  const unsaved = sessionView({ session: { state: "idle", id: null, started_at: null, last: { id: null, exercise: "pushup", total_reps: 3, avg_score: null, saved: false } } });
  assert.deepEqual(unsaved.last, { id: null, saved: false, text: "푸시업 3회" });
  assert.equal(sessionView({ session: { state: "active", id: 9, started_at: "x", last: null } }).active, true);
});

test("exercise buttons are locked while a workout is running", () => {
  const base = { exercises: ["pushup", "kneeup"], current: "pushup", pending: null, online: true };
  assert.deepEqual(_selectorView(base).map((b) => b.disabled), [false, false]);
  assert.deepEqual(_selectorView({ ...base, locked: true }).map((b) => b.disabled), [true, true]);
  assert.equal(_selectorView({ ...base, locked: true })[0].selected, true);
});

test("session errors become guidance, not status codes", () => {
  const e = (status, message) => ({ status, message });
  assert.match(sessionErrorText(e(409, "Analyzer is offline")), /꺼져 있어요/);
  assert.match(sessionErrorText(e(409, "A workout session is already running")), /이미 운동 중/);
  assert.match(sessionErrorText(e(409, "No workout session is running")), /진행 중인 운동이 없어요/);
  assert.match(sessionErrorText(e(409, "Analyzer does not support workout sessions (update it)")), /업데이트/);
  assert.match(sessionErrorText(e(503, "Broker is not connected")), /MQTT/);
  assert.equal(sessionErrorText({ message: "서버에 연결할 수 없습니다" }), "서버에 연결할 수 없습니다");
  assert.equal(sessionErrorText(null), "요청을 처리하지 못했어요");
});

test("analysisView hides counts and elapsed time while idle and tells the user what to do", () => {
  const info = (over) => ({ info: { exercise: "pushup", calibrated: true, elapsed_seconds: 42, visible: true, phase: "up", reps: 0,
    primary: { feature: "elbow_angle", value: 160 }, features: {}, thresholds: { up: 150, count: 140, target: 130 }, live_feedback: [], checks: [],
    last_rep: null, rep_scores: [], session: { state: "idle", id: null, started_at: null, last: null }, ...over }, online: true });
  const idle = analysisView(info());
  assert.equal(idle.idle, true);
  assert.equal(idle.elapsed, "--:--");
  assert.deepEqual(idle.ready, { tone: "good", text: "준비됐어요 · 시작을 눌러 주세요" });
  assert.equal(analysisView(info({ visible: false })).ready.tone, "warn");
  const active = analysisView(info({ session: { state: "active", id: 3, started_at: "x", last: null } }));
  assert.equal(active.ready, null);
  assert.equal(active.elapsed, "00:42");
  assert.equal(analysisView(info({ session: undefined })).ready, null);
});
