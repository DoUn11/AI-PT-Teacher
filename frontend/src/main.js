import { Api, AuthError } from "./api.js";
import { CameraSession } from "./camera.js";
import { loadDashboard } from "./dashboard.js";
import { exerciseLabel, h } from "./dom.js";

const $ = (id) => document.getElementById(id);
const api = new Api();
let messages = {};
let configs = {};
let camera = null;

async function loadJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} 를 불러오지 못했습니다 (${res.status})`);
  return res.json();
}

// ---- 화면 전환 ----------------------------------------------------------

function showAuth(message = "") {
  stopCamera();
  api.logout();
  $("auth").hidden = false;
  $("app").hidden = true;
  $("logout").hidden = true;
  $("who").textContent = "";
  $("auth-error").textContent = message;
}

async function showApp() {
  const me = await api.me(); // 토큰이 유효한지 확인하면서 사용자 정보를 얻는다
  $("auth").hidden = true;
  $("app").hidden = false;
  $("logout").hidden = false;
  $("who").textContent = me.nickname; // textContent: 닉네임을 HTML로 해석하지 않는다
  showTab("camera");
}

function showTab(name) {
  if (name !== "camera") stopCamera();
  for (const tab of ["camera", "dashboard"]) {
    $(`tab-${tab}`).hidden = tab !== name;
    $(`btn-${tab}`).setAttribute("aria-selected", String(tab === name));
  }
  if (name === "dashboard") loadDashboard($("tab-dashboard"), { api, messages, onAuthError: () => showAuth("로그인이 만료되었습니다.") });
}

// ---- 로그인 / 회원가입 ----------------------------------------------------

async function submitAuth(register) {
  const email = $("email").value.trim();
  const password = $("password").value;
  $("auth-error").textContent = "";
  api.setBase($("server").value);
  try {
    if (register) await api.register(email, password, $("nickname").value.trim() || email.split("@")[0]);
    await api.login(email, password);
    $("password").value = "";
    await showApp();
  } catch (e) {
    $("auth-error").textContent = e.message;
  }
}

// ---- 카메라 -------------------------------------------------------------

function setCameraUi(running) {
  $("start").disabled = running;
  $("stop").disabled = !running;
  $("exercise").disabled = running;
  $("upload").disabled = running;
}

function stopCamera() {
  if (camera?.running) $("stop").click();
}

async function startCamera() {
  const cfg = configs[$("exercise").value];
  $("reps").replaceChildren();
  $("result").textContent = "";
  $("upload-status").textContent = "";
  setCameraUi(true);
  camera = new CameraSession({
    video: $("video"),
    canvas: $("overlay"),
    messages,
    onEvent: (ev) => {
      if (ev.type === "status") $("status").textContent = ev.text;
      if (ev.type === "rep") {
        const fb = ev.rep.feedback.map((c) => messages[c] ?? c).join(", ");
        $("reps").prepend(h("li", {}, `${ev.rep.summary.index}회 · ${ev.rep.score}점${fb ? ` · ${fb}` : ""}`));
      }
      if (ev.type === "upload") {
        $("upload-status").textContent = ev.error
          ? `업로드 대기 ${ev.pending}개 (${ev.error})`
          : ev.pending ? `업로드 대기 ${ev.pending}개` : "저장됨";
      }
    },
  });
  try {
    await camera.start(cfg, $("upload").checked ? api : null);
  } catch (e) {
    setCameraUi(false);
    $("status").textContent = "";
    if (e instanceof AuthError) return showAuth("로그인이 만료되었습니다.");
    $("result").textContent = e.message;
  }
}

async function stopCameraClicked() {
  $("stop").disabled = true;
  const cam = camera;
  const finished = await cam.stop();
  const reps = cam.reps;
  const avg = reps.length ? Math.round(reps.reduce((s, r) => s + r.score, 0) / reps.length) : null;
  $("status").textContent = "";
  $("result").textContent = reps.length
    ? `${exerciseLabel(cam.cfg.exercise)} ${reps.length}회 · 평균 ${avg}점${cam.uploader ? (finished ? " · 기록에 저장됨" : " · 저장 실패(위 업로드 상태 확인)") : " · 저장하지 않음"}`
    : "완료된 반복이 없습니다.";
  setCameraUi(false);
}

// ---- 시작 ---------------------------------------------------------------

async function init() {
  $("server").value = api.base;
  $("login-form").addEventListener("submit", (e) => { e.preventDefault(); submitAuth(false); });
  $("register").addEventListener("click", () => submitAuth(true));
  $("logout").addEventListener("click", () => showAuth());
  $("btn-camera").addEventListener("click", () => showTab("camera"));
  $("btn-dashboard").addEventListener("click", () => showTab("dashboard"));
  $("start").addEventListener("click", startCamera);
  $("stop").addEventListener("click", stopCameraClicked);

  try {
    [configs.squat, configs.pushup, messages] = await Promise.all([
      loadJson("./configs/squat.json"), loadJson("./configs/pushup.json"), loadJson("./configs/messages.ko.json"),
    ]);
  } catch (e) {
    $("auth-error").textContent = e.message;
    return;
  }
  if (api.loggedIn) {
    try { await showApp(); return; } catch (e) { /* 만료/서버 꺼짐: 로그인 화면으로 */ }
  }
  showAuth();
}

init();
