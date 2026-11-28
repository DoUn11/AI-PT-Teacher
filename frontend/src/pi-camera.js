// Pi 카메라 화면: 백엔드가 MQTT로 받은 영상(MJPEG)과 연결 상태, 최근 연결 기록
import { AuthError } from "./api.js?v=20261129.1";
import { h } from "./dom.js?v=20261129.1";
import { createAnalysisPanel } from "./analysis-panel.js?v=20261129.1";

const STATUS_POLL_MS = 2000;
const ANALYSIS_POLL_MS = 400; // 횟수·각도가 실시간처럼 보이도록 영상 상태보다 자주
const EVENTS_REFRESH_MS = 10000;
const RETRY_STREAM_MS = 3000;

export const EVENT_LABELS = {
  stream_online: "Pi 카메라 연결됨",
  stream_offline: "Pi 카메라 신호 끊김",
  analysis_online: "분석기 연결됨",
  analysis_offline: "분석기 신호 끊김",
  broker_connected: "브로커 연결됨",
  broker_disconnected: "브로커 연결 끊김",
};

// 백엔드 상태 -> 화면 문구와 색조(ok | warn | off). 서버 값은 텍스트로만 쓴다.
export function describeStatus(s) {
  if (!s.enabled) return { tone: "off", text: "서버에서 카메라 수신이 꺼져 있습니다 (MQTT_URL 설정 필요)" };
  if (!s.broker_connected) return { tone: "warn", text: "브로커에 연결되지 않았습니다" };
  if (!s.online) {
    return { tone: "warn", text: s.frames_total ? "Pi 신호가 끊겼습니다" : "브로커 연결됨 · Pi 영상을 기다리는 중" };
  }
  return { tone: "ok", text: `수신 중 · ${s.fps}fps` };
}

const fmtTime = (iso) => new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "medium" });

// root 안에 화면을 만들고 { start, stop }을 돌려준다. 탭을 떠나면 stop()으로 스트림 연결을 끊는다.
export function mountPiCamera(root, { api, onAuthError, navigate = null, messages = {} }) {
  const badge = h("span", { class: "badge", role: "status" }, "확인 중");
  const img = h("img", { alt: "Pi 카메라 실시간 영상", hidden: true });
  const placeholder = h("div", { class: "stage-note muted" }, "영상을 기다리는 중");
  const statusText = h("p", { class: "muted", "aria-live": "polite" }, "");
  const eventList = h("ul", { class: "event-list" });
  // 로그인이 만료됐으면 화면을 로그인으로 보내고, 그 밖의 오류는 패널이 사용자 문구로 바꿔 보여 주도록 던진다
  const guarded = (call) => async (...args) => {
    try {
      return await call(...args);
    } catch (e) {
      if (e instanceof AuthError) { onAuthError(); return undefined; }
      throw e;
    }
  };
  // 운동은 '운동' 탭의 카드에서 골라 시작하므로 이 패널에는 종료와 휴식 건너뛰기만 있다
  const panel = createAnalysisPanel(messages, {
    onStop: guarded(() => api.stopWorkout()),
    onSkipRest: guarded(() => api.skipRest()),
    onReport: (id) => navigate?.("dashboard", { session: id }), // 내가 운동을 끝내면(또는 목표를 다 채우면) 바로 리포트로
  });
  // 영상 종류: 기본은 자동(분석기가 켜져 있으면 분석 화면, 아니면 원본). 직접 고르면 그대로 유지한다.
  const sourceButtons = {
    analyzed: h("button", { type: "button", class: "ghost", "aria-pressed": "false" }, "분석 화면"),
    raw: h("button", { type: "button", class: "ghost", "aria-pressed": "false" }, "원본"),
  };
  // 왼쪽에 큰 영상, 오른쪽에 분석 패널. 연결 기록은 부차 정보라 한 화면 밖(아래)에 둔다.
  root.replaceChildren(
    h("div", { class: "pi-layout" },
      h("div", { class: "card pi-camera" },
        h("div", { class: "stage" }, badge, placeholder, img),
        h("div", { class: "camera-toolbar" }, statusText,
          h("div", { class: "seg", role: "group", "aria-label": "영상 종류" }, sourceButtons.analyzed, sourceButtons.raw))),
      panel.el),
    h("div", { class: "card pi-events" }, h("h2", {}, "연결 기록"), eventList));

  let timers = [];
  let running = false;
  let streaming = false;
  let lastOnline = null;
  let eventsAt = 0;
  let streamFailed = false; // 영상 연결이 실패했으면 상태 문구에 같이 알린다
  let picked = null; // 사용자가 고른 영상 종류 (null이면 자동)
  let analyzedOnline = false;
  let source = null; // 지금 열려 있는 영상 종류
  const effective = () => picked ?? (analyzedOnline ? "analyzed" : "raw");

  const setImg = (src) => {
    if (src) img.src = src;
    else img.removeAttribute("src"); // 연결을 확실히 끊는다
    img.hidden = !src;
  };

  function openStream() {
    streaming = true;
    source = effective();
    for (const [key, button] of Object.entries(sourceButtons)) button.setAttribute("aria-pressed", String(key === source));
    setImg(`${api.base}/api/v1/camera/stream?source=${source}&token=${encodeURIComponent(api.token)}&t=${Date.now()}`);
  }

  for (const key of Object.keys(sourceButtons)) {
    sourceButtons[key].addEventListener("click", () => {
      picked = key;
      if (running && source !== key) { setImg(null); openStream(); }
    });
  }

  img.addEventListener("load", () => { streamFailed = false; });
  img.addEventListener("error", () => {
    if (!running) return;
    streamFailed = true;
    setImg(null);
    streaming = false;
    // 서버가 재시작되는 등 끊긴 경우: 잠시 뒤 다시 연다 (다음 상태 확인에서 로그인 만료도 걸러낸다)
    timers.push(setTimeout(() => { if (running && !streaming) openStream(); }, RETRY_STREAM_MS));
  });

  async function refreshEvents() {
    eventsAt = Date.now();
    const events = await api.cameraEvents(10);
    eventList.replaceChildren(...(events.length
      ? events.map((e) => h("li", {}, h("span", {}, EVENT_LABELS[e.event] ?? e.event), h("time", { class: "muted" }, fmtTime(e.created_at))))
      : [h("li", { class: "muted" }, "아직 기록이 없습니다")]));
  }

  async function poll() {
    if (!running) return;
    try {
      const s = await api.cameraStatus();
      if (!running) return;
      const d = describeStatus(s);
      statusText.textContent = streamFailed && d.tone !== "off" ? `${d.text} · 영상 연결 실패, 다시 시도 중` : d.text;
      badge.textContent = { ok: "수신 중", warn: "신호 없음", off: "꺼짐" }[d.tone];
      badge.dataset.tone = d.tone;
      placeholder.hidden = d.tone === "ok";
      placeholder.textContent = d.tone === "off" ? "카메라 수신이 꺼져 있습니다" : "영상을 기다리는 중";
      analyzedOnline = s.analyzed.online;
      if (s.enabled && (!streaming || source !== effective())) openStream(); // 자동 모드에서 분석기가 켜지거나 꺼지면 영상도 바꾼다
      if (lastOnline !== s.online || Date.now() - eventsAt > EVENTS_REFRESH_MS) {
        lastOnline = s.online;
        await refreshEvents();
      }
    } catch (e) {
      if (e instanceof AuthError) return onAuthError();
      statusText.textContent = e.message;
      badge.textContent = "오류";
      badge.dataset.tone = "warn";
    }
    if (running) timers.push(setTimeout(poll, STATUS_POLL_MS));
  }

  async function pollAnalysis() {
    if (!running) return;
    try {
      const data = await api.cameraAnalysis();
      panel.update(data);
    } catch (e) {
      if (e instanceof AuthError) return onAuthError();
      // 그 밖의 오류는 영상 상태 줄이 알려 준다
    }
    if (running) timers.push(setTimeout(pollAnalysis, ANALYSIS_POLL_MS));
  }

  return {
    start() {
      if (running) return;
      running = true;
      lastOnline = null;
      poll();
      pollAnalysis();
    },
    stop() {
      running = false;
      streaming = false;
      source = null;
      panel.stop();
      timers.forEach(clearTimeout);
      timers = [];
      setImg(null);
    },
  };
}
