// 서버에서 온 값(닉네임, 피드백 코드 등)을 안전하게 그리기 위한 DOM 헬퍼.
// 문자열은 항상 텍스트 노드로 들어가므로 innerHTML을 쓰지 않는다.
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props ?? {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const EXERCISE_LABELS = { pushup: "푸시업", kneeup: "스탠딩 니업", sidecrunch: "스탠딩 사이드 크런치" };
export const exerciseLabel = (key) => EXERCISE_LABELS[key] ?? key;

export const PLANNED_EXERCISE_LABELS = {};
export const DISPLAY_EXERCISE_LABELS = { ...EXERCISE_LABELS, ...PLANNED_EXERCISE_LABELS };
