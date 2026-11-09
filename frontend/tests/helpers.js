// analyzer/tests/helpers.py 와 같은 합성 자세 (왼쪽 몸만 잘 보임)
const rad = (d) => (d * Math.PI) / 180;

function pose(points) {
  const xy = {};
  const conf = {};
  for (const side of ["left", "right"]) {
    for (const j of ["shoulder", "elbow", "wrist", "hip", "knee", "ankle"]) {
      xy[`${side}_${j}`] = [0, 0];
      conf[`${side}_${j}`] = 0.05;
    }
  }
  for (const [j, p] of Object.entries(points)) {
    xy[`left_${j}`] = p;
    conf[`left_${j}`] = 0.9;
  }
  return { xy, conf };
}

export function squatPose(knee, lean = 0) {
  const k = rad(knee);
  const l = rad(lean);
  const hip = [100 + 100 * Math.sin(k), 200 + 100 * Math.cos(k)];
  return pose({
    ankle: [100, 300],
    knee: [100, 200],
    hip,
    shoulder: [hip[0] + 100 * Math.sin(l), hip[1] - 100 * Math.cos(l)],
  });
}

export function pushupPose(elbow, hipOffset = 0) {
  const e = rad(elbow);
  const elbowP = [400, 360];
  return pose({
    ankle: [0, 300],
    shoulder: [400, 300],
    hip: [200, 300 + hipOffset],
    elbow: elbowP,
    wrist: [elbowP[0] + 60 * Math.sin(e), elbowP[1] - 60 * Math.cos(e)],
  });
}

// top에서 시작해 bottom까지 내려갔다 top으로 돌아오는 값 (코사인 곡선)
export function cycle(top, bottom, frames = 30) {
  return Array.from({ length: frames + 1 }, (_, i) =>
    bottom + ((top - bottom) * (1 + Math.cos((2 * Math.PI * i) / frames))) / 2);
}

export const FPS = 30;

export function run(analyzer, poses, startT = 0) {
  return poses.map((p, i) => analyzer.process(p, startT + i / FPS));
}

export const repsOf = (frames) => frames.map((f) => f.rep).filter(Boolean);
