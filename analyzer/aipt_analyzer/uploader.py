"""백엔드로 결과 업로드.

BackendClient: 동기 클라이언트 (로그인, 세션 시작/종료, rep 일괄 전송).
UploadWorker : 영상 루프를 막지 않도록 백그라운드 스레드에서 BackendClient를 돌린다.
실패한 rep은 대기열에 남겨 다음 기회에 다시 보낸다 (서버가 같은 index를 무시하므로 안전).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

import httpx

from aipt_analyzer.results import RepResult

log = logging.getLogger("aipt.upload")

# 재시도해도 소용없는 클라이언트 오류 (잘못된 요청, 세션 없음/종료 등)
_PERMANENT = {400, 403, 404, 409, 422}


class BackendClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._api = base_url.rstrip("/") + "/api/v1"
        self._email = email
        self._password = password
        self._http = client or httpx.Client(timeout=timeout)
        self._token: str | None = None
        self._lock = threading.Lock()
        self._pending: list[dict] = []
        self.session_id: int | None = None

    def login(self) -> None:
        r = self._http.post(f"{self._api}/auth/login", json={"email": self._email, "password": self._password})
        r.raise_for_status()
        self._token = r.json()["access_token"]

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        """액세스 토큰이 만료돼 401이 나오면 한 번 다시 로그인해서 재시도한다."""
        for attempt in (1, 2):
            if self._token is None:
                self.login()
            r = self._http.request(
                method, f"{self._api}{path}", headers={"Authorization": f"Bearer {self._token}"}, **kw
            )
            if r.status_code == 401 and attempt == 1:
                self._token = None
                continue
            return r
        return r  # pragma: no cover

    def start_session(self, exercise: str) -> int:
        r = self._request("POST", "/workouts/sessions", json={"exercise": exercise})
        r.raise_for_status()
        self.session_id = r.json()["id"]
        return self.session_id

    def enqueue(self, rep: RepResult, completed_at: datetime | None = None) -> None:
        when = completed_at or datetime.now(timezone.utc)
        with self._lock:
            self._pending.append(
                {
                    "index": rep.summary.index,
                    "score": rep.score,
                    "min_angle": round(min(max(rep.summary.primary_min, 0.0), 180.0), 2),
                    "feedback": list(rep.feedback)[:10],
                    "completed_at": when.isoformat(),
                }
            )

    def flush(self) -> bool:
        """대기 중인 rep을 한 번에 보낸다. 모두 처리됐으면(또는 버려졌으면) True."""
        if self.session_id is None:
            return False
        with self._lock:
            batch = list(self._pending)
        if not batch:
            return True
        try:
            r = self._request("POST", f"/workouts/sessions/{self.session_id}/reps", json={"reps": batch})
        except httpx.HTTPError as e:
            log.warning("rep 업로드 실패, 나중에 다시 시도합니다: %s", e)
            return False
        if r.status_code in _PERMANENT:
            log.error("rep 업로드가 거부되어 %d개를 버립니다 (%d): %s", len(batch), r.status_code, r.text)
        elif r.status_code >= 400:
            log.warning("rep 업로드 실패 (%d), 나중에 다시 시도합니다", r.status_code)
            return False
        with self._lock:
            self._pending = self._pending[len(batch):]
        return True

    def finish(self) -> bool:
        if self.session_id is None:
            return False
        try:
            r = self._request("POST", f"/workouts/sessions/{self.session_id}/finish")
        except httpx.HTTPError as e:
            log.warning("세션 종료 요청 실패: %s", e)
            return False
        return r.status_code < 400

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)


class UploadWorker:
    def __init__(self, client: BackendClient, retry_interval: float = 3.0) -> None:
        self._client = client
        self._retry = retry_interval
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> "UploadWorker":
        self._thread.start()
        return self

    def submit(self, rep: RepResult) -> None:
        self._client.enqueue(rep)
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=self._retry)
            self._wake.clear()
            if self._client.pending_count:
                self._client.flush()

    def close(self, final_flush_tries: int = 3) -> None:
        """남은 rep을 보내고 세션을 종료한다."""
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=5.0)
        for _ in range(final_flush_tries):
            if self._client.flush():
                break
        if self._client.pending_count:
            log.error("업로드하지 못한 rep %d개가 남았습니다 (--record를 켰다면 reps.jsonl에 있습니다)",
                      self._client.pending_count)
        self._client.finish()
