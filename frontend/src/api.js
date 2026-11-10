// 백엔드(/api/v1) 클라이언트.
const TOKEN_KEY = "aipt_token"; // 탭을 닫으면 사라지도록 sessionStorage에만 둔다
export const DEFAULT_BASE = "http://127.0.0.1:8000";

export class AuthError extends Error {} // 토큰이 없거나 만료됨 -> 다시 로그인
export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// 저장소가 막힌 환경(프라이빗 모드 등)에서도 동작하도록 감싼다.
const store = (storage) => ({
  get: (k) => {
    try { return storage.getItem(k); } catch { return null; }
  },
  set: (k, v) => {
    try { storage.setItem(k, v); } catch { /* 저장 못 해도 동작은 한다 */ }
  },
  del: (k) => {
    try { storage.removeItem(k); } catch { /* noop */ }
  },
});
const session = store(globalThis.sessionStorage ?? { getItem() {}, setItem() {}, removeItem() {} });

function describe(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg ?? JSON.stringify(d)).join("; ");
  return "요청을 처리하지 못했습니다";
}

export class Api {
  constructor(base = DEFAULT_BASE) {
    this.base = base;
  }

  get token() { return session.get(TOKEN_KEY); }
  get loggedIn() { return Boolean(this.token); }
  logout() { session.del(TOKEN_KEY); }

  async request(method, path, body, { auth = true } = {}) {
    const headers = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (auth) {
      if (!this.token) throw new AuthError("로그인이 필요합니다");
      headers.Authorization = `Bearer ${this.token}`;
    }
    let res;
    try {
      res = await fetch(`${this.base}/api/v1${path}`, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new ApiError(0, `서버에 연결할 수 없습니다 (${this.base})`);
    }
    if (res.status === 401 && auth) {
      this.logout();
      throw new AuthError("로그인이 만료되었습니다");
    }
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new ApiError(res.status, describe(data?.detail));
    }
    return res.json();
  }

  async register(email, password, nickname) {
    return this.request("POST", "/auth/register", { email, password, nickname }, { auth: false });
  }

  async login(email, password) {
    const { access_token } = await this.request("POST", "/auth/login", { email, password }, { auth: false });
    session.set(TOKEN_KEY, access_token);
  }

  me() { return this.request("GET", "/users/me"); }
  createSession(exercise) { return this.request("POST", "/workouts/sessions", { exercise }); }
  addReps(id, reps) { return this.request("POST", `/workouts/sessions/${id}/reps`, { reps }); }
  finishSession(id) { return this.request("POST", `/workouts/sessions/${id}/finish`); }
  sessions(limit = 20) { return this.request("GET", `/workouts/sessions?limit=${limit}`); }
  session(id) { return this.request("GET", `/workouts/sessions/${id}`); }
  stats() { return this.request("GET", "/workouts/stats"); }
}
