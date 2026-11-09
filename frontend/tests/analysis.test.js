import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { Analyzer, angleDeg, extractFeatures, validateConfig, verticalLeanDeg } from "../src/analysis.js";
import { cycle, pushupPose, repsOf, run, squatPose } from "./helpers.js";

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
  for (const name of ["squat", "pushup", "messages.ko"]) {
    const mine = readFileSync(new URL(`../configs/${name}.json`, import.meta.url), "utf8");
    const origin = readFileSync(new URL(`../../analyzer/aipt_analyzer/configs/${name}.json`, import.meta.url), "utf8");
    assert.equal(mine, origin, `${name}.json drifted from analyzer; re-copy it`);
  }
  validateConfig(load("squat"));
  validateConfig(load("pushup"));
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

test("pushups: good form vs sagging body line", () => {
  const cfg = load("pushup");
  const good = repsOf(run(new Analyzer(cfg), cycle(170, 80).map((e) => pushupPose(e))));
  assert.equal(good.length, 1);
  assert.ok(good[0].feedback.length === 0 && good[0].score >= 95);

  const sag = repsOf(run(new Analyzer(cfg), cycle(170, 80).map((e) => pushupPose(e, 60))));
  assert.equal(sag.length, 1);
  assert.ok(sag[0].feedback.includes("body_line") && sag[0].score <= 80);
});
