import { ApiError } from "./api.js";

// 재시도해도 소용없는 클라이언트 오류 (잘못된 요청, 세션 없음/종료 등)
const PERMANENT = new Set([400, 403, 404, 409, 422]);

// 끝난 rep을 백엔드로 올린다. 실패하면 대기열에 남겨 다음 기회에 다시 보낸다
// (서버가 같은 index를 무시하므로 재전송해도 안전).
export class RepUploader {
  constructor(api, sessionId) {
    this.api = api;
    this.sessionId = sessionId;
    this.pending = [];
    this.lastError = null;
    this._busy = false;
  }

  enqueue(rep) {
    this.pending.push({
      index: rep.summary.index,
      score: rep.score,
      min_angle: Math.round(Math.min(Math.max(rep.summary.primaryMin, 0), 180) * 100) / 100,
      feedback: rep.feedback.slice(0, 10),
      completed_at: new Date().toISOString(), // 항상 Z(UTC) 포함 — 서버는 시간대 없는 시각을 거부한다
    });
  }

  // 대기 중인 rep을 한 번에 보낸다. 처리됐으면(또는 버려졌으면) true.
  async flush() {
    if (this._busy) return false;
    if (this.pending.length === 0) return true;
    this._busy = true;
    const batch = this.pending.slice();
    try {
      await this.api.addReps(this.sessionId, batch);
      this.pending.splice(0, batch.length);
      this.lastError = null;
      return true;
    } catch (e) {
      this.lastError = e.message;
      if (e instanceof ApiError && PERMANENT.has(e.status)) {
        this.pending.splice(0, batch.length); // 다시 보내도 안 되므로 버린다
        return true;
      }
      return false;
    } finally {
      this._busy = false;
    }
  }

  // 남은 rep을 보내고 세션을 종료한다. 종료된 세션 정보를 돌려준다(실패하면 null).
  async finish(tries = 3) {
    for (let i = 0; i < tries && this.pending.length > 0; i++) {
      if (await this.flush()) break;
    }
    try {
      return await this.api.finishSession(this.sessionId);
    } catch (e) {
      this.lastError = e.message;
      return null;
    }
  }
}
