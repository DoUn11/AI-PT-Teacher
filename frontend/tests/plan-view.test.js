import assert from "node:assert/strict";
import { test } from "node:test";

import {
  cardView, changeView, estimate, formatCountdown, formatRest, needsSave, normalizeSetup, planResultView, progressView, setupDefaults, volumeText,
} from "../src/plan-view.js";

const plan = (over = {}) => ({
  exercise: "pushup", sets: 3, reps: 10, rest_seconds: 60, pace: "normal", auto_progress: true, is_default: false, suggestion: null, last_change: null,
  limits: { floor: 4, ceiling: 15, max_sets: 5 }, last_session: null, ...over,
});
const change = (over = {}) => ({ session_id: 4, change: "up", reason: "ready_up", need_more: null, before: { sets: 3, reps: 10 }, after: { sets: 3, reps: 11 }, applied: true, ...over });

test("volume and rest read naturally", () => {
  assert.equal(volumeText(plan()), "3세트 × 10회");
  assert.deepEqual([0, 45, 60, 90, 120].map(formatRest), ["휴식 없음", "휴식 45초", "휴식 1분", "휴식 90초", "휴식 2분"]);
  assert.deepEqual([estimate({ sets: 3, reps: 10, rest_seconds: 60 }), estimate({ sets: 1, reps: 1, rest_seconds: 0 })], [{ total: 30, minutes: 4 }, { total: 1, minutes: 1 }]);
});

test("every evaluation reason the backend can send has a sentence", () => {
  const reasons = ["ready_up", "new_set", "wait_more", "low_quality", "incomplete", "struggled", "at_floor", "at_max"];
  for (const reason of reasons) assert.ok(changeView(change({ reason, need_more: 2 })).text.length > 5, reason);
  assert.match(changeView(change()).text, /3세트 × 11회/);
  assert.match(changeView(change({ change: "hold", reason: "wait_more", need_more: 2 })).text, /2번 더 잘하면/);
  assert.equal(changeView(null), null);
});

test("an unapplied suggestion is worded as a recommendation, an applied change as a fact", () => {
  const applied = changeView(change());
  assert.deepEqual([applied.tone, applied.suggestOnly, applied.target], ["up", false, "3세트 × 11회"]);
  const suggested = changeView(change({ applied: false }));
  assert.equal(suggested.suggestOnly, true);
  assert.match(suggested.text, /추천 목표는 3세트 × 11회/);
  const down = changeView(change({ change: "down", reason: "struggled", after: { sets: 3, reps: 9 }, applied: false }));
  assert.match(down.text, /낮추는 걸 추천해요/);
  assert.equal(changeView(change({ change: "hold", reason: "low_quality", applied: false })).suggestOnly, false, "hold는 제안이 아니다");
  assert.equal(changeView(change({ change: "hold", reason: "wait_more", need_more: 1 })).target, null);
});

test("card shows target, tags, last result and the latest evaluation", () => {
  const v = cardView(plan({ is_default: true, last_session: { id: 1, started_at: "2026-11-28T01:00:00Z", reps_done: 24, target_total: 30, avg_score: 81.6 }, last_change: change() }));
  assert.equal(v.name, "푸시업");
  assert.deepEqual([v.volume, v.rest, v.pace, v.auto, v.isDefault], ["3세트 × 10회", "휴식 1분", "보통", true, true]);
  assert.deepEqual(v.parts, ["팔"]);
  assert.deepEqual([v.last.text, v.last.tier], ["지난번 24회 · 82점", "good"]);
  assert.equal(v.change.target, "3세트 × 11회");
  const sug = cardView(plan({ suggestion: change({ applied: false }), last_change: change({ change: "hold", reason: "wait_more", need_more: 1 }) }));
  assert.equal(sug.change.suggestOnly, true, "제안이 있으면 평가보다 제안을 먼저 보여 준다");
  assert.equal(cardView(plan()).last, null);
  assert.deepEqual(cardView(plan({ exercise: "sidecrunch" })).parts, ["팔", "다리", "몸통"]);
});

test("setup values are clamped to the exercise limits and only saved when something changed", () => {
  const lim = { floor: 4, ceiling: 15, max_sets: 5 };
  assert.deepEqual(normalizeSetup({ sets: 9, reps: 0, rest_seconds: 999, pace: "turbo", auto_progress: 1 }, lim), { sets: 5, reps: 1, rest_seconds: 600, pace: "normal", auto_progress: true });
  assert.deepEqual(normalizeSetup({ sets: "3", reps: "12.4", rest_seconds: 45, pace: "fast", auto_progress: false }, lim), { sets: 3, reps: 12, rest_seconds: 45, pace: "fast", auto_progress: false });
  const p = plan();
  const same = setupDefaults(p);
  assert.equal(needsSave(p, same), false);
  assert.equal(needsSave(p, { ...same, pace: "fast" }), true);
  assert.equal(needsSave(p, { ...same, reps: 11 }), true);
  assert.equal(needsSave(plan({ is_default: true }), same), true, "처음에는 추천 시작값도 저장해야 평가가 시작된다");
});

test("countdown formatting rounds up so it never shows 0:00 early", () => {
  assert.deepEqual([37.2, 0.2, 0, 65, null].map(formatCountdown), ["0:38", "0:01", "0:00", "1:05", "0:00"]);
});

const progress = (over = {}) => ({ sets: 3, reps: 10, rest_seconds: 45, set_index: 2, set_reps: 4, reps_done: 14, status: "working", remaining: null, ...over });

test("progress view: working shows set and rep progress without a countdown", () => {
  const v = progressView(progress());
  assert.deepEqual([v.title, v.repsText, v.repsPct, v.headline, v.countdown, v.skippable], ["2 / 3 세트", "4 / 10회", 40, null, null, false]);
  assert.deepEqual(v.dots.map((d) => d.state), ["done", "current", "todo"]);
  assert.equal(v.last, false);
  assert.equal(progressView(progress({ set_index: 3 })).last, true);
  assert.equal(progressView(null), null);
});

test("progress view: resting counts down to the next set, ready says what is about to start", () => {
  const rest = progressView(progress({ status: "resting", set_reps: 10, remaining: 37.2 }));
  assert.deepEqual([rest.headline, rest.countdown, rest.next, rest.skippable, rest.repsPct], ["휴식 중", "0:38", "다음: 3세트 · 10회", true, 100]);
  assert.deepEqual(rest.dots.map((d) => d.state), ["done", "done", "todo"], "쉬는 중에는 방금 끝낸 세트도 완료로 센다");
  assert.equal(rest.last, false);
  const ready = progressView(progress({ status: "ready", set_index: 1, set_reps: 0, reps_done: 0, remaining: 4.1 }));
  assert.deepEqual([ready.headline, ready.countdown, ready.next, ready.skippable], ["자세를 잡아 주세요", "0:05", "3세트 × 10회를 시작해요", true]);
});

test("plan result: per-set reps with partial sets marked", () => {
  const v = planResultView({ sets: 3, reps: 10, rest_seconds: 45, completed_sets: 1, completion: 0.467, per_set: [{ set: 1, reps: 10, avg_score: 91.2 }, { set: 2, reps: 4, avg_score: 58 }] });
  assert.deepEqual([v.title, v.target, v.pct, v.complete], ["1 / 3 세트 완료", "3세트 × 10회 · 휴식 45초", 47, false]);
  assert.deepEqual(v.perSet.map((s) => [s.text, s.score, s.tier, s.partial]), [["10 / 10회", 91, "good", false], ["4 / 10회", 58, "bad", true]]);
  assert.equal(planResultView(null), null);
  assert.equal(planResultView({ sets: 1, reps: 5, rest_seconds: null, completed_sets: 1, completion: 1, per_set: [] }).complete, true);
});
