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
  return { side: bestSide, values };
}

// 설정 검증 (잘못된 JSON은 시작할 때 알려준다)
export function validateConfig(cfg) {
  const need = ["exercise", "primary_feature", "up_threshold", "count_threshold", "depth"];
  for (const k of need) if (cfg[k] === undefined) throw new Error(`invalid exercise config: missing ${k}`);
  if (!(cfg.primary_feature in FEATURES)) throw new Error(`unknown primary_feature ${cfg.primary_feature}`);
  if (!(cfg.count_threshold < cfg.up_threshold)) throw new Error("count_threshold must be lower than up_threshold");
  const smoothing = cfg.smoothing ?? 0.6;
  if (!(smoothing > 0 && smoothing <= 1)) throw new Error("smoothing must be in (0, 1]");
  for (const c of cfg.form_checks ?? []) {
    if (!(c.feature in FEATURES)) throw new Error(`form check ${c.code}: unknown feature ${c.feature}`);
    if (c.stat !== "min" && c.stat !== "max") throw new Error(`form check ${c.code}: stat must be min or max`);
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

const violates = (stat, limit, value) => (stat === "max" ? value > limit : value < limit);

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
      if (!stat) continue; // 이 반복에서 해당 관절이 안 보였으면 판단하지 않는다
      if (violates(c.stat, c.limit, stat[c.stat])) {
        feedback.push(c.code);
        formPenalty += c.penalty;
      }
    }
    const score = Math.round(Math.max(0, Math.min(100, 100 - depthPenalty - formPenalty)));
    return { score, feedback };
  }

  liveFeedback(values) {
    const codes = [];
    for (const c of this.cfg.form_checks ?? []) {
      const v = values[c.feature];
      if (v !== undefined && violates(c.stat, c.limit, v)) codes.push(c.code);
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
