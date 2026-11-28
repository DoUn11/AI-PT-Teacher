// 몸 그림: 팔·몸통·다리를 약점 정도에 따라 초록 -> 노랑 -> 빨강으로 칠한다 (리포트와 홈에서 같은 그림을 쓴다).
const NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  el.append(...children);
  return el;
}

const LABELS = { arms: "팔", torso: "몸통", legs: "다리" };

// parts: partView() 목록({part, hue, level, label}). 값이 없는 부위는 회색으로 둔다.
export function createBodyMap({ onHover = null } = {}) {
  const groups = {
    arms: svg("g", { "data-part": "arms", class: "bm-limb" },
      svg("title", {}, "팔"),
      svg("path", { d: "M62 100 L46 154 L38 210" }), svg("path", { d: "M158 100 L174 154 L182 210" })),
    torso: svg("g", { "data-part": "torso", class: "bm-torso" },
      svg("title", {}, "몸통"),
      svg("path", { d: "M72 88 Q110 74 148 88 L142 132 L136 206 Q110 218 84 206 L78 132 Z" })),
    legs: svg("g", { "data-part": "legs", class: "bm-limb bm-legs" },
      svg("title", {}, "다리"),
      svg("path", { d: "M94 214 L90 300 L88 374" }), svg("path", { d: "M126 214 L130 300 L132 374" })),
  };
  const root = svg("svg", { class: "bodymap", viewBox: "0 0 220 400", role: "img", "aria-label": "부위별 자세 약점" },
    svg("circle", { class: "bm-head", cx: 110, cy: 42, r: 24 }),
    svg("rect", { class: "bm-head", x: 102, y: 62, width: 16, height: 18, rx: 6 }),
    groups.legs, groups.arms, groups.torso);

  if (onHover) {
    for (const [part, g] of Object.entries(groups)) {
      g.addEventListener("mouseenter", () => onHover(part));
      g.addEventListener("mouseleave", () => onHover(null));
    }
  }

  function update(parts) {
    const known = new Map((parts ?? []).map((p) => [p.part, p]));
    const summary = [];
    for (const [part, g] of Object.entries(groups)) {
      const p = known.get(part);
      if (p) {
        g.style.setProperty("--h", String(p.hue));
        g.dataset.level = p.level;
        summary.push(`${LABELS[part]} ${p.levelLabel}`);
      } else {
        g.style.removeProperty("--h");
        delete g.dataset.level;
      }
    }
    root.setAttribute("aria-label", summary.length ? `부위별 자세: ${summary.join(", ")}` : "아직 기록이 없어요");
    root.classList.toggle("is-empty", known.size === 0);
  }

  function highlight(part) {
    for (const [name, g] of Object.entries(groups)) g.classList.toggle("is-hot", name === part);
  }

  return { el: root, update, highlight };
}
