import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { Analyzer, ThresholdJudge, angleDeg, extractFeatures, validateConfig, verticalLeanDeg } from "../src/analysis.js";
import { cycle, frontalPose, pushupPose, repsOf, run, squatPose } from "./helpers.js";

const load = (name) => JSON.parse(readFileSync(new URL(`../configs/${name}.json`, import.meta.url), "utf8"));
const near = (actual, expected, tol) => assert.ok(Math.abs(actual - expected) <= tol, `${actual} !~ ${expected}`);

test("angle and lean geometry", () => {
  near(angleDeg([0, 1], [0, 0], [1, 0]), 90, 1e-9);
  near(angleDeg([1, 0], [0, 0], [-1, 0]), 180, 1e-9);
  assert.equal(angleDeg([0, 0], [0, 0], [1, 0]), null);
  near(verticalLeanDeg([0, 10], [0, 0]), 0, 1e-9);
  near(verticalLeanDeg([0, 10], [10, 0]), 45, 1e-9);
});

test("extractFeatures recovers known angles and skips unseen joints", () => {
  const f = extractFeatures(squatPose(120, 30));
  assert.equal(f.side, "left");
  near(f.values.knee_angle, 120, 0.01);
  near(f.values.trunk_lean, 30, 0.01);
  assert.ok(!("elbow_angle" in f.values));

  const p = squatPose(120);
  p.conf.left_knee = 0.1;
  assert.ok(!("knee_angle" in extractFeatures(p).values));
});

test("config files are valid and identical to the analyzer's copies", () => {
  for (const name of ["squat", "pushup", "kneeup", "sidecrunch", "messages.ko"]) {
    const mine = readFileSync(new URL(`../configs/${name}.json`, import.meta.url), "utf8");
    const origin = readFileSync(new URL(`../../analyzer/aipt_analyzer/configs/${name}.json`, import.meta.url), "utf8");
    assert.equal(mine, origin, `${name}.json drifted from analyzer; re-copy it`);
  }
  for (const name of ["squat", "pushup", "kneeup", "sidecrunch"]) validateConfig(load(name));
  const messages = load("messages.ko");
  for (const name of ["pushup", "kneeup", "sidecrunch"]) {
    const cfg = load(name);
    for (const c of cfg.form_checks) assert.ok(c.code in messages, `${name}: no message for ${c.code}`);
    assert.ok(cfg.form_checks.filter((c) => ["gaze_front", "leg_tension"].includes(c.code)).every((c) => c.enabled === false));
  }
});

test("invalid config is rejected", () => {
  const base = load("squat");
  assert.throws(() => validateConfig({ ...base, count_threshold: 170 }));
  assert.throws(() => validateConfig({ ...base, smoothing: 0 }));
  assert.throws(() => validateConfig({ ...base, primary_feature: "nope" }));
  assert.throws(() => validateConfig({ ...base, form_checks: [{ code: "x", feature: "trunk_lean", stat: "avg", limit: 1, penalty: 1 }] }));
  assert.throws(() => validateConfig({ exercise: "x" }));
});

test("good squats are counted and scored high", () => {
  const a = new Analyzer(load("squat"));
  const frames = run(a, [0, 1, 2].flatMap(() => cycle(172, 90).map((k) => squatPose(k, 20))));
  const reps = repsOf(frames);
  assert.deepEqual(reps.map((r) => r.summary.index), [1, 2, 3]);
  assert.equal(frames.at(-1).reps, 3);
  for (const r of reps) {
    assert.ok(r.score >= 95 && r.feedback.length === 0);
    near(r.summary.primaryMin, 90, 3);
  }
});

test("shallow squat counts but gets feedback and a lower score", () => {
  const reps = repsOf(run(new Analyzer(load("squat")), cycle(172, 125).map((k) => squatPose(k))));
  assert.equal(reps.length, 1);
  assert.ok(reps[0].feedback.includes("shallow_depth"));
  assert.ok(reps[0].score < 70);
});

test("excess trunk lean is flagged (same score as the Python analyzer: 85)", () => {
  const reps = repsOf(run(new Analyzer(load("squat")), cycle(172, 90).map((k) => squatPose(k, 70))));
  assert.equal(reps.length, 1);
  assert.deepEqual(reps[0].feedback, ["trunk_lean"]);
  assert.equal(reps[0].score, 85);
});

test("partial dip, jitter and starting seated are not reps", () => {
  const cfg = load("squat");
  assert.equal(repsOf(run(new Analyzer(cfg), cycle(172, 145).map((k) => squatPose(k)))).length, 0);
  assert.equal(repsOf(run(new Analyzer(cfg), cycle(172, 90, 6).map((k) => squatPose(k)))).length, 0);
  const seated = run(new Analyzer(cfg), Array(5).fill(squatPose(100)));
  assert.ok(seated.every((f) => f.phase === "waiting"));
});

test("lost visibility mid-rep does not break counting", () => {
  const poses = cycle(172, 90).map((k) => squatPose(k));
  for (let i = 10; i < 14; i++) poses[i] = null;
  const frames = run(new Analyzer(load("squat")), poses);
  assert.ok(frames.slice(10, 14).every((f) => !f.visible));
  assert.equal(repsOf(frames).length, 1);
});

test("live feedback appears only while moving", () => {
  const frames = run(new Analyzer(load("squat")), cycle(172, 90).map((k) => squatPose(k, 70)));
  assert.deepEqual(frames[0].liveFeedback, []);
  assert.ok(frames.some((f) => f.liveFeedback.includes("trunk_lean")));
});

test("start count continues numbering", () => {
  const reps = repsOf(run(new Analyzer(load("squat"), null, 4), cycle(172, 90).map((k) => squatPose(k))));
  assert.equal(reps[0].summary.index, 5);
});

test("pushups are counted and shallow ones penalised lightly", () => {
  const cfg = load("pushup");
  const good = repsOf(run(new Analyzer(cfg), cycle(170, 80).map((e) => pushupPose(e))));
  assert.equal(good.length, 1);
  assert.deepEqual([good[0].score, good[0].feedback], [100, []]);

  // 몸 일직선(body_line)은 정면 카메라에서 판정하지 않으므로 꺼져 있다
  const sag = repsOf(run(new Analyzer(cfg), cycle(170, 80).map((e) => pushupPose(e, 60))));
  assert.equal(sag.length, 1);
  assert.deepEqual(sag[0].feedback, []);

  // 얕은 푸시업도 1회로 세고(count_threshold 140), 약한 조건이라 조금만 깎는다.
  // 2초짜리 동작: 1초면 150도 아래에 머무는 시간이 min_rep_seconds(0.6초)보다 짧아 떨림으로 버려진다
  const shallow = repsOf(run(new Analyzer(cfg), cycle(170, 136, 60).map((e) => pushupPose(e))));
  assert.equal(shallow.length, 1);
  assert.deepEqual([shallow[0].score, shallow[0].feedback], [90, ["shallow_depth"]]);
});

// ---- 약한 조건 보정 칸 (enabled, live, full_at, stat mean, score_step) ----
// 파이썬 분석기 tests/test_core.py 의 같은 이름 테스트와 숫자가 같아야 한다.

const judgeWith = (checks, extra = {}) => new ThresholdJudge(validateConfig({ ...load("squat"), form_checks: checks, ...extra }));
const repAt = (lean) => ({ primaryMin: 90, stats: { trunk_lean: { min: lean, max: lean, mean: lean } } });
const lean = (more = {}) => ({ code: "trunk_lean", feature: "trunk_lean", stat: "max", limit: 40, penalty: 20, ...more });

test("disabled check is not judged, penalised or shown live", () => {
  const j = judgeWith([lean({ enabled: false })]);
  assert.deepEqual(j.judge(repAt(80)), { score: 100, feedback: [] });
  assert.deepEqual(j.liveFeedback({ trunk_lean: 80 }), []);
});

test("live=false keeps the after-rep feedback but drops the live warning", () => {
  const j = judgeWith([lean({ live: false })]);
  assert.deepEqual(j.judge(repAt(50)), { score: 80, feedback: ["trunk_lean"] });
  assert.deepEqual(j.liveFeedback({ trunk_lean: 50 }), []);
});

test("full_at grades the penalty by how far past the limit", () => {
  const j = judgeWith([lean({ full_at: 60 })]);
  assert.equal(j.judge(repAt(45)).score, 95); // 1/4 -> 5점
  assert.equal(j.judge(repAt(50)).score, 90); // 1/2 -> 10점
  assert.equal(j.judge(repAt(90)).score, 80); // full_at 넘으면 penalty 전부
  assert.equal(j.judge(repAt(40)).score, 100); // 기준선은 위반 아님
});

test("direction flips which side of the limit is a violation", () => {
  const j = judgeWith([lean({ direction: "below" })]); // 최댓값이 40에 못 미치면 위반
  assert.deepEqual(j.judge(repAt(30)), { score: 80, feedback: ["trunk_lean"] });
  assert.deepEqual(j.judge(repAt(50)), { score: 100, feedback: [] });
});

test("stat mean uses direction", () => {
  const j = judgeWith([{ code: "low", feature: "trunk_lean", stat: "mean", direction: "below", limit: 10, penalty: 30 }]);
  assert.deepEqual(j.judge(repAt(5)), { score: 70, feedback: ["low"] });
  assert.deepEqual(j.judge(repAt(15)), { score: 100, feedback: [] });
});

test("score_step rounds the rep score to 10-point steps", () => {
  const j = judgeWith([lean({ full_at: 60 })], { score_step: 10 });
  assert.equal(j.judge(repAt(44)).score, 100); // 96 -> 100
  assert.equal(j.judge(repAt(46)).score, 90); // 94 -> 90
  assert.equal(j.judge(repAt(50)).score, 90);
  assert.equal(j.judge(repAt(90)).score, 80);
});

test("invalid weak-condition fields are rejected", () => {
  const base = load("squat");
  const bad = (c, extra = {}) => () => validateConfig({ ...base, form_checks: [lean(c)], ...extra });
  assert.throws(bad({ full_at: 30 })); // max 조건인데 full_at이 limit보다 작음
  assert.throws(bad({ stat: "mean" })); // mean인데 direction 없음
  assert.throws(bad({ direction: "sideways" }));
  assert.throws(bad({ tier: "great" }));
  assert.throws(bad({ penalty: -1 }));
  assert.throws(bad({}, { score_step: 0 }));
  validateConfig({ ...base, form_checks: [lean({ tier: "weak", live: false, full_at: 60, enabled: true })], score_step: 10 });
});

// ---- 정면 촬영용 양쪽 몸·얼굴 특징, 니업 (analyzer/tests/test_core.py 와 같은 숫자) ----

test("body features on a frontal pose", () => {
  const v = extractFeatures(frontalPose(-0.9)).values;
  const expected = { torso_sw: 2.5, knee_gap: 0.9, hip_flex: 177.137595, knee_lat: 0, ankle_lift: 0, head_pitch: 0.02,
    head_yaw: 0, elbow_angle_both: 175.030259, shoulder_angle_both: 14.172338, hand_ratio: 1.75 };
  for (const [k, x] of Object.entries(expected)) near(v[k], x, 1e-5);
  const lifted = extractFeatures(frontalPose(0.1)).values;
  near(lifted.knee_gap, -0.1, 1e-9);
  near(lifted.ankle_lift, 1, 1e-9);
});

test("body features skip when the face is not visible", () => {
  const p = frontalPose();
  p.conf.nose = 0.1;
  const v = extractFeatures(p).values;
  assert.ok(!("head_pitch" in v) && !("head_yaw" in v) && "torso_sw" in v);
});

test("knee-up counts lifts and flags a low knee", () => {
  let a = new Analyzer(load("kneeup"));
  const good = repsOf(run(a, [0, 1].flatMap(() => cycle(-0.9, 0).map((k) => frontalPose(k)))));
  assert.equal(good.length, 2);
  for (const r of good) assert.deepEqual([r.score, r.feedback], [100, []]);

  a = new Analyzer(load("kneeup"));
  const low = repsOf(run(a, cycle(-0.9, -0.38).map((k) => frontalPose(k))));
  assert.equal(low.length, 1); // 무릎이 덜 올라와도 1회로 센다
  assert.deepEqual([low[0].score, low[0].feedback], [70, ["knee_low"]]);
});
