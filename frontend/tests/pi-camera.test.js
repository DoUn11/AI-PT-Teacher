import assert from "node:assert/strict";
import { test } from "node:test";

import { EVENT_LABELS, describeStatus } from "../src/pi-camera.js";

const base = { enabled: true, broker_connected: true, online: true, fps: 14.2, last_frame_age_seconds: 0, frames_total: 10 };

test("describeStatus covers every state the backend can report", () => {
  assert.deepEqual(describeStatus(base), { tone: "ok", text: "수신 중 · 14.2fps" });
  assert.equal(describeStatus({ ...base, enabled: false }).tone, "off");
  assert.equal(describeStatus({ ...base, broker_connected: false }).tone, "warn");
  assert.match(describeStatus({ ...base, online: false, frames_total: 0 }).text, /기다리는 중/);
  assert.match(describeStatus({ ...base, online: false }).text, /끊겼습니다/);
});

test("every backend event type has a label", () => {
  for (const e of ["stream_online", "stream_offline", "analysis_online", "analysis_offline", "broker_connected", "broker_disconnected"]) {
    assert.ok(EVENT_LABELS[e], e);
  }
});
