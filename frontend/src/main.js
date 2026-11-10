import "./appearance.js?v=20261110.14";
import { createExercisePicker } from "./exercise-picker.js?v=20261110.14";
import { Api, AuthError, DEFAULT_BASE } from "./api.js";
import { CameraSession } from "./camera.js?v=20261110.14";
import { loadDashboard } from "./dashboard.js?v=20261110.14";
import { EXERCISE_LABELS, DISPLAY_EXERCISE_LABELS, PLANNED_EXERCISE_LABELS, exerciseLabel, h } from "./dom.js?v=20261110.14";

const $ = (id) => document.getElementById(id);
const api = new Api(DEFAULT_BASE);
let messages = {};
let configs = {};
let camera = null;
let registering = false;
let exercisePicker;

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
  $("btn-camera").removeAttribute("aria-current");
  $("btn-dashboard").removeAttribute("aria-current");
  $("logout").hidden = true;
  $("who").textContent = "";
  $("auth-error").textContent = message;
}

async function showApp() {
  const me = await api.me(); // 토큰이 유효한지 확인하면서 사용자 정보를 얻는다
  $("auth").hidden = true;
  $("app").hidden = false;
  $("main-nav").hidden = false;
  $("logout").hidden = false;
  $("who").textContent = me.nickname; // textContent: 닉네임을 HTML로 해석하지 않는다
  showTab(new URLSearchParams(location.search).get("tab") === "dashboard" ? "dashboard" : "camera");
}

function showTab(name) {
  const url = new URL(location.href);
  url.searchParams.set("tab", name);
  history.replaceState(null, "", url);
  window.scrollTo({ top: 0, behavior: "instant" });
  exercisePicker?.close();
  if (name !== "camera") stopCamera();
  for (const tab of ["camera", "dashboard"]) {
    $(`tab-${tab}`).hidden = tab !== name;
    $(`btn-${tab}`).toggleAttribute("aria-current", tab === name);
    if (tab === name) $(`btn-${tab}`).setAttribute("aria-current", "page");
  }
  if (name === "dashboard") loadDashboard($("tab-dashboard"), { api, exerciseLabels: DISPLAY_EXERCISE_LABELS, messages, onAuthError: () => showAuth("로그인이 만료되었습니다.") });
}

// ---- 로그인 / 회원가입 ----------------------------------------------------

async function submitAuth(register) {
  if (!$("login-form").reportValidity()) return;
  const buttons = $("login-form").querySelectorAll("button");
  buttons.forEach((button) => button.disabled = true);
  const email = $("email").value.trim();
  const password = $("password").value;
  $("auth-error").textContent = "";
  try {
    if (register) await api.register(email, password, $("nickname").value.trim() || email.split("@")[0]);
    await api.login(email, password);
    $("password").value = "";
    await showApp();
  } catch (e) {
    $("auth-error").textContent = e.message;
  } finally {
    buttons.forEach((button) => button.disabled = false);
  }
}

// ---- 카메라 -------------------------------------------------------------

function setCameraUi(running) {
  $("status").textContent = running ? "준비 중" : "정지";
  exercisePicker?.close();
  $("camera-placeholder").hidden = running;
  $("camera-state").classList.toggle("is-running", running);
  $("camera-state").setAttribute("aria-label", running ? "촬영 중" : "정지");
  $("start").disabled = running;
  $("stop").disabled = !running;
  $("exercise").disabled = running;
  $("upload").disabled = running;
}

function stopCamera() {
  if (camera?.running) $("stop").click();
}

async function startCamera() {
  const cfg = configs[$("exercise-select").value];
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
        $("reps").prepend(h("li", {}, `${ev.rep.summary.index}회 | ${ev.rep.score}점${fb ? ` | ${fb}` : ""}`));
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
    $("status").textContent = "연결 실패";
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
    ? `${exerciseLabel(cam.cfg.exercise)} ${reps.length}회 | 평균 ${avg}점${cam.uploader ? (finished ? " | 기록에 저장됨" : " | 저장 실패(위 업로드 상태 확인)") : " | 저장하지 않음"}`
    : "완료된 반복이 없습니다.";
  setCameraUi(false);
}

// ---- 시작 ---------------------------------------------------------------

async function init() {
  $("login-form").addEventListener("submit", (e) => { e.preventDefault(); submitAuth(registering); });
  $("register").addEventListener("click", () => {
    registering = !registering;
    $("auth-title").textContent = $("auth-submit").textContent = registering ? "회원가입" : "로그인";
    $("register").textContent = registering ? "로그인" : "회원가입";
    $("nickname-field").hidden = !registering;
    $("password").autocomplete = registering ? "new-password" : "current-password";
    $("auth-error").textContent = "";
  });
  $("logout").addEventListener("click", () => showAuth());
  for (const tab of ["camera", "dashboard"]) {
    $(`btn-${tab}`).addEventListener("click", (event) => {
      event.preventDefault();
      if ($("app").hidden) return;
      showTab(tab);
    });
  }
  $("auth-submit").disabled = false;
  $("register").disabled = false;
  $("start").addEventListener("click", startCamera);
  $("stop").addEventListener("click", stopCameraClicked);

  try {
    const exercises = Object.keys(EXERCISE_LABELS);
    const values = await Promise.all(exercises.map((key) => loadJson(`./configs/${key}.json`)));
    configs = Object.fromEntries(exercises.map((key, i) => [key, values[i]]));
    messages = await loadJson("./configs/messages.ko.json");
    const choices = Object.entries(DISPLAY_EXERCISE_LABELS)
      .map(([value, label]) => ({ value, label, disabled: value in PLANNED_EXERCISE_LABELS }));
    exercisePicker = createExercisePicker(choices);
  } catch (e) {
    showAuth(e.message);
    return;
  }
  if (api.loggedIn) {
    try { await showApp(); return; } catch (e) { /* 만료/서버 꺼짐: 로그인 화면으로 */ }
  }
  showAuth();
}

await init();
