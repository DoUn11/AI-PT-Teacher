import { h } from "./dom.js?v=20261110.14";

export function createExercisePicker(choices, settings = {}) {
  const events = new AbortController();
  const button = settings.button ?? document.getElementById("exercise-select");
  const value = settings.value ?? document.getElementById("exercise-value");
  const list = settings.list ?? document.getElementById("exercise-options");
  const picker = button.parentElement;
  const options = choices.map((choice, index) => h("button", {
    type: "button", role: "option", id: `${list.id}-option-${index}`, tabindex: "-1",
    value: choice.value, disabled: choice.disabled, "aria-selected": "false",
    onClick: () => { select(choice); close(true); settings.onChange?.(choice.value); },
  }, h("span", {}, choice.label), h("span", { class: "option-check", "aria-hidden": "true" }, "✓")));
  list.replaceChildren(...options);
  function select(choice) {
    button.value = choice.value;
    value.textContent = choice.label;
    options.forEach((option) => option.setAttribute("aria-selected", String(option.value === choice.value)));
  }
  function close(restoreFocus = false) {
    list.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (restoreFocus) button.focus();
  }
  function position() {
    if (list.hidden) return;
    const rect = button.getBoundingClientRect();
    const scale = rect.width / button.offsetWidth;
    const below = innerHeight - rect.bottom - 16;
    const above = rect.top - document.querySelector(".header").getBoundingClientRect().bottom - 16;
    const upwards = below < Math.min(list.scrollHeight, 280) * scale && above > below;
    list.classList.toggle("above", upwards);
    list.style.maxHeight = `${Math.max(48, Math.min(280, (upwards ? above : below) / scale))}px`;
  }
  function open(last = false) {
    if (button.matches(":disabled")) return;
    list.hidden = false;
    button.setAttribute("aria-expanded", "true");
    position();
    const enabled = options.filter((option) => !option.disabled);
    const target = last ? enabled.at(-1) : enabled.find((option) => option.value === button.value) ?? enabled[0];
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: "nearest" });
  }
  button.addEventListener("click", () => list.hidden ? open() : close());
  button.addEventListener("keydown", (event) => {
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault(); open(event.key === "ArrowUp" || event.key === "End");
    }
  });
  picker.addEventListener("keydown", (event) => {
    if (list.hidden) return;
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(true); }
    if (event.target === button) return;
    const enabled = options.filter((option) => !option.disabled);
    let index = enabled.indexOf(document.activeElement);
    if (event.key === "Home") index = 0;
    else if (event.key === "End") index = enabled.length - 1;
    else if (event.key === "ArrowDown") index = (index + 1) % enabled.length;
    else if (event.key === "ArrowUp") index = (index - 1 + enabled.length) % enabled.length;
    else return;
    event.preventDefault(); enabled[index]?.focus();
  });
  document.addEventListener("pointerdown", (event) => { if (!picker.contains(event.target)) close(); }, { signal: events.signal });
  picker.addEventListener("focusout", (event) => { if (!picker.contains(event.relatedTarget)) close(); });
  window.addEventListener("resize", position, { signal: events.signal });
  window.addEventListener("scroll", position, { capture: true, signal: events.signal });
  select(choices.find((choice) => choice.value === settings.selected && !choice.disabled) ?? choices.find((choice) => !choice.disabled));
  return { close, destroy() { close(); events.abort(); } };
}
