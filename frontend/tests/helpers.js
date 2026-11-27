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

// analyzer/tests/helpers.py frontal_pose 와 같은 정면 자세 (전부 잘 보임, 몸통 100px, 어깨 폭 40px)
export function frontalPose(kneeH = -0.9, shin = 0.9) {
  const kneeY = 200 - kneeH * 100;
  const xy = {
    nose: [100, 60], left_ear: [110, 62], right_ear: [90, 62],
    left_shoulder: [120, 100], right_shoulder: [80, 100], left_elbow: [130, 150], right_elbow: [70, 150],
    left_wrist: [135, 195], right_wrist: [65, 195], left_hip: [115, 200], right_hip: [85, 200],
    left_knee: [115, kneeY], right_knee: [85, 290], left_ankle: [115, kneeY + shin * 100], right_ankle: [85, 380],
  };
  return { xy, conf: Object.fromEntries(Object.keys(xy).map((k) => [k, 0.9])) };
}
