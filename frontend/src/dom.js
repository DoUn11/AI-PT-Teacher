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

export const EXERCISE_LABELS = { squat: "스쿼트", pushup: "푸시업" };
export const exerciseLabel = (key) => EXERCISE_LABELS[key] ?? key;

export const PLANNED_EXERCISE_LABELS = { lunge: "런지 (추가 예정)", plank: "플랭크 (추가 예정)", crunch: "크런치 (추가 예정)" };
export const DISPLAY_EXERCISE_LABELS = { ...EXERCISE_LABELS, ...PLANNED_EXERCISE_LABELS };
