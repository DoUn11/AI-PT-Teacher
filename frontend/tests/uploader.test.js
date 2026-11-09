import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError } from "../src/api.js";
import { RepUploader } from "../src/uploader.js";

const rep = (index, score = 90, primaryMin = 95.123, feedback = []) => ({
  summary: { index, primaryMin },
  score,
  feedback,
});

// 호출 결과를 순서대로 돌려주는 가짜 Api. 값이 Error면 던진다.
function fakeApi({ reps = [], finish = [{ status: "finished" }] } = {}) {
  const calls = { addReps: [], finish: 0 };
  const next = (queue) => {
    const v = queue.length > 1 ? queue.shift() : queue[0];
    if (v instanceof Error) throw v;
    return v;
  };
  return {
    calls,
    async addReps(id, batch) {
      calls.addReps.push({ id, batch: structuredClone(batch) });
      return next(reps.length ? reps : [{}]);
    },
    async finishSession() {
      calls.finish += 1;
      return next(finish);
    },
  };
}

test("payload matches the backend contract", async () => {
  const api = fakeApi();
  const up = new RepUploader(api, 7);
  up.enqueue(rep(2, 77, 118.456, ["shallow_depth"]));
  up.enqueue(rep(3, 60, 250, [])); // 범위를 벗어난 각도는 180으로 제한
  assert.equal(await up.flush(), true);

  const { id, batch } = api.calls.addReps[0];
  assert.equal(id, 7);
  assert.deepEqual(
    { index: batch[0].index, score: batch[0].score, min_angle: batch[0].min_angle, feedback: batch[0].feedback },
    { index: 2, score: 77, min_angle: 118.46, feedback: ["shallow_depth"] });
  assert.equal(batch[1].min_angle, 180);
  assert.match(batch[0].completed_at, /Z$/); // 시간대 없는 시각은 서버가 거부한다
  assert.equal(up.pending.length, 0);
});

test("network failure keeps reps and the next flush resends them together", async () => {
  const api = fakeApi({ reps: [new ApiError(0, "서버에 연결할 수 없습니다"), {}] });
  const up = new RepUploader(api, 1);
  up.enqueue(rep(1));
  assert.equal(await up.flush(), false);
  assert.equal(up.pending.length, 1);
  assert.match(up.lastError, /연결/);

  up.enqueue(rep(2));
  assert.equal(await up.flush(), true);
  assert.deepEqual(api.calls.addReps[1].batch.map((r) => r.index), [1, 2]);
  assert.equal(up.lastError, null);
});

test("permanent rejections drop the batch instead of retrying forever", async () => {
  for (const status of [409, 422, 404]) {
    const up = new RepUploader(fakeApi({ reps: [new ApiError(status, "no")] }), 1);
    up.enqueue(rep(1));
    assert.equal(await up.flush(), true);
    assert.equal(up.pending.length, 0, `status ${status}`);
  }
});

test("server errors and auth errors are retried later", async () => {
  const up = new RepUploader(fakeApi({ reps: [new ApiError(503, "down")] }), 1);
  up.enqueue(rep(1));
  assert.equal(await up.flush(), false);
  assert.equal(up.pending.length, 1);
});

test("concurrent flush calls do not send duplicates", async () => {
  const api = fakeApi();
  const up = new RepUploader(api, 1);
  up.enqueue(rep(1));
  const results = await Promise.all([up.flush(), up.flush()]);
  assert.equal(api.calls.addReps.length, 1);
  assert.deepEqual(results, [true, false]); // 두 번째 호출은 진행 중이라 건너뜀
});

test("finish flushes what is left and then finishes the session", async () => {
  const api = fakeApi();
  const up = new RepUploader(api, 5);
  up.enqueue(rep(1));
  const result = await up.finish();
  assert.deepEqual(result, { status: "finished" });
  assert.equal(api.calls.addReps.length, 1);
  assert.equal(api.calls.finish, 1);
});

test("finish returns null when the session cannot be closed", async () => {
  const up = new RepUploader(fakeApi({ finish: [new ApiError(500, "boom")] }), 5);
  assert.equal(await up.finish(), null);
  assert.equal(up.lastError, "boom");
});
