const root = document.documentElement;
const toggles = document.querySelectorAll("[data-theme-toggle]");
const systemDark = matchMedia("(prefers-color-scheme: dark)").matches;
function apply(theme) {
  const dark = theme === "dark";
  root.dataset.theme = dark ? "dark" : "light";
  toggles.forEach(toggle => {
    toggle.setAttribute("aria-pressed", String(dark));
    toggle.setAttribute("aria-label", dark ? "하얀 화면으로 전환" : "검은 화면으로 전환");
  });
}
let saved;
try { saved = localStorage.getItem("aipt_theme"); } catch { /* Storage may be unavailable. */ }
apply(["light", "dark"].includes(saved) ? saved : systemDark ? "dark" : "light");
toggles.forEach(toggle => toggle.addEventListener("click", () => {
  apply(root.dataset.theme === "dark" ? "light" : "dark");
  try { localStorage.setItem("aipt_theme", root.dataset.theme); } catch { /* Optional persistence. */ }
}));
const header = document.querySelector(".header");
new ResizeObserver(() => root.style.setProperty("--header-height", `${header.getBoundingClientRect().height}px`)).observe(header);
