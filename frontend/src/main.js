import "./appearance.js?v=20261129.1";
import { Api, AuthError, DEFAULT_BASE } from "./api.js?v=20261129.1";
import { loadDashboard } from "./dashboard.js?v=20261129.1";
import { loadHome } from "./home.js?v=20261129.1";
import { mountWorkout } from "./workout.js?v=20261129.1";
import { DISPLAY_EXERCISE_LABELS } from "./dom.js?v=20261129.1";

const $ = (id) => document.getElementById(id);
const api = new Api(DEFAULT_BASE);
let messages = {};
let registering = false;
let piCamera = null;
const TABS = ["home", "pi", "dashboard"];
const DEFAULT_TAB = "home";

async function loadJson(path) {
  // 설정·문구 JSON은 주소에 버전이 없어서, 캐시된 옛 문구를 쓰지 않게 매번 서버에 확인한다
  const res = await fetch(path, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${path} 를 불러오지 못했습니다 (${res.status})`);
  return res.json();
}

// ---- 화면 전환 ----------------------------------------------------------

function showAuth(message = "") {
  piCamera?.stop();
  api.logout();
  $("auth").hidden = false;
  $("app").hidden = true;
  for (const tab of TABS) $(`btn-${tab}`).removeAttribute("aria-current");
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
  const wanted = new URLSearchParams(location.search).get("tab");
  showTab(TABS.includes(wanted) ? wanted : DEFAULT_TAB);
}

// 화면 이동. params의 값은 주소(?tab=...&session=3)에 실려 새로고침해도 같은 화면이 열린다. null이면 뺀다.
function navigate(tab, params = {}) {
  const url = new URL(location.href);
  url.search = "";
  url.searchParams.set("tab", tab);
  for (const [key, value] of Object.entries(params)) if (value != null) url.searchParams.set(key, String(value));
  history.pushState(null, "", url);
  showTab(tab);
}

function showTab(name) {
  const url = new URL(location.href);
  url.searchParams.set("tab", name);
  history.replaceState(null, "", url);
  window.scrollTo({ top: 0, behavior: "instant" });
  if (name !== "pi") piCamera?.stop();
  for (const tab of TABS) {
    $(`tab-${tab}`).hidden = tab !== name;
    $(`btn-${tab}`).toggleAttribute("aria-current", tab === name);
    if (tab === name) $(`btn-${tab}`).setAttribute("aria-current", "page");
  }
  const ctx = { api, messages, navigate, onAuthError: () => showAuth("로그인이 만료되었습니다.") };
  if (name === "home") loadHome($("tab-home"), ctx);
  if (name === "pi") {
    piCamera ??= mountWorkout($("tab-pi"), ctx);
    piCamera.preselect(new URLSearchParams(location.search).get("exercise"));
    piCamera.start();
  }
  if (name === "dashboard") loadDashboard($("tab-dashboard"), { ...ctx, exerciseLabels: DISPLAY_EXERCISE_LABELS });
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
  for (const tab of TABS) {
    $(`btn-${tab}`).addEventListener("click", (event) => {
      event.preventDefault();
      if ($("app").hidden) return;
      navigate(tab);
    });
  }
  window.addEventListener("popstate", () => {
    if ($("app").hidden) return;
    const wanted = new URLSearchParams(location.search).get("tab");
    showTab(TABS.includes(wanted) ? wanted : DEFAULT_TAB);
  });
  $("auth-submit").disabled = false;
  $("register").disabled = false;

  try {
    messages = await loadJson("./configs/messages.ko.json");
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
