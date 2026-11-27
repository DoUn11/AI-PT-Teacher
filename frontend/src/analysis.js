// 분석기(analyzer/aipt_analyzer)의 판정 로직을 브라우저용으로 옮긴 것.
// 같은 설정 JSON(configs/*.json)을 읽고, 같은 순서로 계산한다:
//   pose -> 특징(관절 각도) -> 스무딩 -> rep 상태 머신 -> 판정
// pose 형식: { xy: { left_hip: [x, y], ... }, conf: { left_hip: 0.9, ... } }  (픽셀 좌표)

const SIDE_JOINTS = ["shoulder", "elbow", "wrist", "hip", "knee", "ankle"];
const SMOOTHING_RESET_GAP_S = 1.0;

export function angleDeg(a, b, c) {
  const v1 = [a[0] - b[0], a[1] - b[1]];
  const v2 = [c[0] - b[0], c[1] - b[1]];
  const n1 = Math.hypot(...v1);
  const n2 = Math.hypot(...v2);
  if (n1 < 1e-6 || n2 < 1e-6) return null;
  const cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2);
  return (Math.acos(Math.max(-1, Math.min(1, cos))) * 180) / Math.PI;
}

// base -> top 선분이 수직(위쪽)에서 벗어난 각도. 이미지 y축은 아래가 +.
export function verticalLeanDeg(base, top) {
  const v = [top[0] - base[0], top[1] - base[1]];
  const n = Math.hypot(...v);
  if (n < 1e-6) return null;
  return (Math.acos(Math.max(-1, Math.min(1, -v[1] / n))) * 180) / Math.PI;
}

export const FEATURES = {
  knee_angle: { joints: ["hip", "knee", "ankle"], compute: (p) => angleDeg(p[0], p[1], p[2]) },
  hip_angle: { joints: ["shoulder", "hip", "knee"], compute: (p) => angleDeg(p[0], p[1], p[2]) },
  elbow_angle: { joints: ["shoulder", "elbow", "wrist"], compute: (p) => angleDeg(p[0], p[1], p[2]) },
  body_line: { joints: ["shoulder", "hip", "ankle"], compute: (p) => angleDeg(p[0], p[1], p[2]) },
  trunk_lean: { joints: ["hip", "shoulder"], compute: (p) => verticalLeanDeg(p[0], p[1]) },
};

// ---- 정면 촬영용 양쪽 몸·얼굴 특징 (analyzer/aipt_analyzer/features.py BODY_FEATURES와 같은 정의) ----
// 이미지 좌표(y가 아래쪽), 길이는 몸통 길이(어깨 중점-골반 중점)로 나눈다. "들어 올린 다리" = 무릎이 더 높이 올라온 쪽.
const sub = (a, b) => [a[0] - b[0], a[1] - b[1]];
const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
const norm = (v) => Math.hypot(v[0], v[1]);
const up = (v) => -v[1];
const meanOrNull = (...xs) => {
  const v = xs.filter((x) => x !== null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};

function body(p) {
  const S = mid(p.left_shoulder, p.right_shoulder);
  const H = mid(p.left_hip, p.right_hip);
  const torso = norm(sub(S, H));
  return torso > 1e-6 ? torso : null;
}
const kneeH = (p, s, torso) => up(sub(p[`${s}_knee`], p[`${s}_hip`])) / torso;
const lifted = (p, torso) => (kneeH(p, "left", torso) >= kneeH(p, "right", torso) ? "left" : "right");
const other = (s) => (s === "left" ? "right" : "left");
const both = (...joints) => ["left", "right"].flatMap((s) => joints.map((j) => `${s}_${j}`));
const TRUNK = both("shoulder", "hip");
const FACE = ["nose", "left_ear", "right_ear"];

function headVec(p) {
  return sub(p.nose, mid(p.left_ear, p.right_ear));
}

export const BODY_FEATURES = {
  torso_sw: { keypoints: TRUNK, compute: (p) => {
    const t = body(p);
    const sw = norm(sub(p.left_shoulder, p.right_shoulder));
    return t && sw > 1e-6 ? t / sw : null;
  } },
  knee_gap: { keypoints: [...TRUNK, ...both("knee")], compute: (p) => {
    const t = body(p);
    return t ? -kneeH(p, lifted(p, t), t) : null;
  } },
  hip_flex: { keypoints: [...TRUNK, ...both("knee")], compute: (p) => {
    const t = body(p);
    if (!t) return null;
    const s = lifted(p, t);
    return angleDeg(p[`${s}_shoulder`], p[`${s}_hip`], p[`${s}_knee`]);
  } },
  knee_lat: { keypoints: [...TRUNK, ...both("knee")], compute: (p) => {
    const t = body(p);
    if (!t) return null;
    const s = lifted(p, t);
    const out = sub(p[`${s}_hip`], p[`${other(s)}_hip`]);
    const n = norm(out);
    if (n < 1e-6) return null;
    const k = sub(p[`${s}_knee`], p[`${s}_hip`]);
    return (k[0] * out[0] + k[1] * out[1]) / n / t;
  } },
  ankle_lift: { keypoints: [...TRUNK, ...both("ankle")], compute: (p) => {
    const t = body(p);
    return t ? Math.abs(up(sub(p.left_ankle, p.right_ankle))) / t : null;
  } },
  head_pitch: { keypoints: [...TRUNK, ...FACE], compute: (p) => {
    const t = body(p);
    return t ? up(headVec(p)) / t : null;
  } },
  head_yaw: { keypoints: [...TRUNK, ...FACE], compute: (p) => {
    const t = body(p);
    const axis = sub(p.left_shoulder, p.right_shoulder);
    const n = norm(axis);
    if (!t || n < 1e-6) return null;
    const v = headVec(p);
    return Math.abs((v[0] * axis[0] + v[1] * axis[1]) / n) / t;
  } },
  elbow_angle_both: { keypoints: both("shoulder", "elbow", "wrist"), compute: (p) => meanOrNull(
    ...["left", "right"].map((s) => angleDeg(p[`${s}_shoulder`], p[`${s}_elbow`], p[`${s}_wrist`])),
  ) },
  shoulder_angle_both: { keypoints: both("hip", "shoulder", "elbow"), compute: (p) => meanOrNull(
    ...["left", "right"].map((s) => angleDeg(p[`${s}_hip`], p[`${s}_shoulder`], p[`${s}_elbow`])),
  ) },
  hand_ratio: { keypoints: both("shoulder", "wrist"), compute: (p) => {
    const sw = norm(sub(p.left_shoulder, p.right_shoulder));
    return sw > 1e-6 ? norm(sub(p.left_wrist, p.right_wrist)) / sw : null;
  } },
};
const ALL_FEATURES = { ...FEATURES, ...BODY_FEATURES };

// 더 잘 보이는 쪽 몸으로 특징을 계산한다. 관절이 안 보이는 특징은 빠진다.
export function extractFeatures(pose, minConf = 0.3) {
  let bestSide = "left";
  let bestScore = -1;
  for (const side of ["left", "right"]) {
    const score = SIDE_JOINTS.reduce((s, j) => s + (pose.conf[`${side}_${j}`] ?? 0), 0) / SIDE_JOINTS.length;
    if (score > bestScore) {
      bestSide = side;
      bestScore = score;
    }
  }
  const values = {};
  for (const [name, def] of Object.entries(FEATURES)) {
    const names = def.joints.map((j) => `${bestSide}_${j}`);
    if (names.some((n) => (pose.conf[n] ?? 0) < minConf)) continue;
    const v = def.compute(names.map((n) => pose.xy[n]));
    if (v !== null) values[name] = v;
  }
  for (const [name, def] of Object.entries(BODY_FEATURES)) {
    if (def.keypoints.some((k) => (pose.conf[k] ?? 0) < minConf || !pose.xy[k])) continue;
    const v = def.compute(pose.xy);
    if (v !== null && Number.isFinite(v)) values[name] = v;
  }
  return { side: bestSide, values };
}

// 설정 검증 (잘못된 JSON은 시작할 때 알려준다)
export function validateConfig(cfg) {
  const need = ["exercise", "primary_feature", "up_threshold", "count_threshold", "depth"];
  for (const k of need) if (cfg[k] === undefined) throw new Error(`invalid exercise config: missing ${k}`);
  if (!(cfg.primary_feature in ALL_FEATURES)) throw new Error(`unknown primary_feature ${cfg.primary_feature}`);
  if (!(cfg.count_threshold < cfg.up_threshold)) throw new Error("count_threshold must be lower than up_threshold");
  const smoothing = cfg.smoothing ?? 0.6;
  if (!(smoothing > 0 && smoothing <= 1)) throw new Error("smoothing must be in (0, 1]");
  const step = cfg.score_step ?? 1;
  if (!(Number.isInteger(step) && step >= 1 && step <= 100)) throw new Error("score_step must be an integer in [1, 100]");
  for (const c of cfg.form_checks ?? []) {
    if (!(c.feature in ALL_FEATURES)) throw new Error(`form check ${c.code}: unknown feature ${c.feature}`);
    if (!["min", "max", "mean"].includes(c.stat)) throw new Error(`form check ${c.code}: stat must be min, max or mean`);
    if (![undefined, "above", "below"].includes(c.direction) || (c.stat === "mean" && c.direction === undefined)) {
      throw new Error(`form check ${c.code}: direction must be above or below (required for stat mean)`);
    }
    if (c.tier !== undefined && !["strong", "medium", "weak"].includes(c.tier)) {
      throw new Error(`form check ${c.code}: tier must be strong, medium or weak`);
    }
    if (!(c.penalty >= 0)) throw new Error(`form check ${c.code}: penalty must be >= 0`);
    if (c.full_at !== undefined && c.full_at > c.limit !== violatesAbove(c)) {
      throw new Error(`form check ${c.code}: full_at must be on the violating side of limit`);
    }
  }
  return cfg;
}

class Stat {
  constructor() {
    this.min = Infinity;
    this.max = -Infinity;
    this.total = 0;
    this.n = 0;
  }
  add(v) {
    this.min = Math.min(this.min, v);
    this.max = Math.max(this.max, v);
    this.total += v;
    this.n += 1;
  }
}

// waiting -> up -> moving/down -> 다시 up이면 한 번 완료
export class RepCounter {
  constructor(cfg, startCount = 0) {
    this.cfg = cfg;
    this.reps = startCount;
    this.phase = "waiting";
    this._state = "waiting";
    this._reached = false;
    this._startT = 0;
    this._stats = {};
  }

  update(values, t) {
    const { up_threshold: up, count_threshold: count, primary_feature: primary } = this.cfg;
    const v = values[primary];
    let summary = null;

    if (this._state === "waiting") {
      if (v >= up) this._state = "up";
    } else if (this._state === "up") {
      if (v < up) {
        this._state = "moving";
        this._reached = false;
        this._startT = t;
        this._stats = {};
      }
    }

    if (this._state === "moving") {
      if (v >= up) {
        summary = this._finish(t);
        this._state = "up";
      } else {
        for (const [name, value] of Object.entries(values)) {
          (this._stats[name] ??= new Stat()).add(value);
        }
        if (v <= count) this._reached = true;
      }
    }

    this.phase = this._state === "moving" ? (v <= count ? "down" : "moving") : this._state;
    return summary;
  }

  _finish(t) {
    const cfg = this.cfg;
    const primary = this._stats[cfg.primary_feature];
    if (!this._reached || !primary || t - this._startT < (cfg.min_rep_seconds ?? 0.4)) return null;
    this.reps += 1;
    const stats = {};
    for (const [name, s] of Object.entries(this._stats)) stats[name] = { min: s.min, max: s.max, mean: s.total / s.n };
    return { index: this.reps, startT: this._startT, endT: t, primaryMin: primary.min, stats };
  }
}

// 설정 형식은 analyzer/aipt_analyzer/config.py FormCheck 설명과 같다.
//   enabled=false: 측정·감점·피드백 안 함 / live=false: 실시간 경고 없이 반복 끝난 뒤 피드백만
//   full_at: limit에서 0, full_at에서 penalty까지 선형으로 감점
//   direction(above|below): 그 방향으로 limit을 넘으면 위반. 생략하면 max는 above, min은 below. stat "mean"은 필수
function violatesAbove(c) {
  return c.direction !== undefined ? c.direction === "above" : c.stat === "max";
}
const violates = (c, value) => (violatesAbove(c) ? value > c.limit : value < c.limit);
const enabled = (c) => c.enabled !== false;

function checkPenalty(c, value) {
  if (c.full_at === undefined) return c.penalty;
  const frac = (value - c.limit) / (c.full_at - c.limit);
  return c.penalty * Math.min(1, Math.max(0, frac));
}

// 파이썬 round()와 같은 은행원 반올림 (x.5는 짝수 쪽). 분석기와 점수가 어긋나지 않게 한다.
function roundHalfEven(x) {
  const f = Math.floor(x);
  const d = x - f;
  if (Math.abs(d - 0.5) < 1e-9) return f % 2 === 0 ? f : f + 1;
  return Math.round(x);
}

// 설정의 깊이 규칙 + 자세 검사로 점수를 매기는 규칙 기반 판정기
export class ThresholdJudge {
  constructor(cfg) {
    this.cfg = cfg;
  }

  judge(summary) {
    const { depth, form_checks: checks = [] } = this.cfg;
    const feedback = [];
    const shortfall = Math.max(0, summary.primaryMin - depth.target);
    const depthPenalty = Math.min(shortfall * depth.penalty_per_degree, 100 - depth.min_score);
    if (summary.primaryMin > depth.target + depth.tolerance) feedback.push(depth.code);

    let formPenalty = 0;
    for (const c of checks) {
      const stat = summary.stats[c.feature];
      if (!enabled(c) || !stat) continue; // 꺼 둔 조건, 이 반복에서 관절이 안 보인 조건은 판단하지 않는다
      const value = stat[c.stat];
      if (violates(c, value)) {
        feedback.push(c.code);
        formPenalty += checkPenalty(c, value);
      }
    }
    const raw = Math.max(0, Math.min(100, 100 - depthPenalty - formPenalty));
    const step = this.cfg.score_step ?? 1;
    const score = roundHalfEven(raw / step) * step;
    // 감점이 반올림으로 0이 될 만큼 기준선 근처면 지적하지 않는다 (100점 = 지적 없음)
    return { score, feedback: score >= 100 ? [] : feedback };
  }

  liveFeedback(values) {
    const codes = [];
    for (const c of this.cfg.form_checks ?? []) {
      if (!enabled(c) || c.live === false) continue;
      const v = values[c.feature];
      if (v !== undefined && violates(c, v)) codes.push(c.code);
    }
    return codes;
  }
}

export class Analyzer {
  constructor(cfg, judge = null, startCount = 0) {
    this.cfg = validateConfig(cfg);
    this.judge = judge ?? new ThresholdJudge(cfg);
    this.counter = new RepCounter(cfg, startCount);
    this._smoothed = {};
    this._lastT = null;
  }

  process(pose, t) {
    const values = pose ? extractFeatures(pose, this.cfg.min_keypoint_conf ?? 0.3).values : {};
    if (!(this.cfg.primary_feature in values)) {
      return {
        visible: false,
        features: { ...this._smoothed },
        phase: this.counter.phase,
        reps: this.counter.reps,
        liveFeedback: [],
        rep: null,
      };
    }
    const smoothed = this._smooth(values, t);
    const summary = this.counter.update(smoothed, t);
    const phase = this.counter.phase;
    const liveFeedback = phase === "moving" || phase === "down" ? this.judge.liveFeedback(smoothed) : [];
    const rep = summary ? { summary, ...this.judge.judge(summary) } : null;
    return { visible: true, features: smoothed, phase, reps: this.counter.reps, liveFeedback, rep };
  }

  _smooth(values, t) {
    if (this._lastT !== null && t - this._lastT > SMOOTHING_RESET_GAP_S) this._smoothed = {};
    this._lastT = t;
    const a = this.cfg.smoothing ?? 0.6;
    const out = {};
    for (const [name, v] of Object.entries(values)) {
      const prev = this._smoothed[name];
      out[name] = prev === undefined ? v : a * v + (1 - a) * prev;
    }
    Object.assign(this._smoothed, out);
    return out;
  }
}
