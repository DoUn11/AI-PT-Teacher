"""저장된 MediaPipe 좌표(클립당 32장)를 앱 분석 코드(aipt_analyzer.Analyzer)에 그대로 넣어 보는 데이터 테스트.

    python replay_app.py --mp mp --labels out_mp --dt 0.25
    python replay_app.py --mp mp --labels out_mp --dt 0.15 0.25 0.4   # 시간 간격 가정을 바꿔 가며

calibrate.py는 1회 데이터를 직접 잘라 임계값을 구했다. 여기서는 앱과 똑같이 스무딩 -> RepCounter(고정 임계,
min_rep_seconds) -> ThresholdJudge를 거쳐서, 실제 앱에서 나올 회 수/점수/피드백을 본다.

시간: 32장은 띄엄띄엄 찍힌 사진이라 실제 간격을 모른다. 사진 간격을 --dt 초로 가정하고, 사진 사이를 선형 보간해
--fps 로 넣는다 (앱은 초당 15~30장을 받으므로 스무딩이 비슷하게 동작하게). 보이지 않는 점은 conf 0으로 넣는다.
정답: 라벨은 클립 단위(이 클립은 무릎이 낮음 등)라, 그 클립에서 센 모든 회에 같은 정답을 준다.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aipt_analyzer.analyzer import Analyzer  # noqa: E402
from aipt_analyzer.config import load_exercise_config  # noqa: E402
from aipt_analyzer.pose import COCO_KEYPOINTS, Pose  # noqa: E402

from calibrate import EXERCISES  # noqa: E402

# COCO 17점 -> MediaPipe 33점 번호
MP_OF_COCO = {"nose": 0, "left_eye": 2, "right_eye": 5, "left_ear": 7, "right_ear": 8,
              "left_shoulder": 11, "right_shoulder": 12, "left_elbow": 13, "right_elbow": 14,
              "left_wrist": 15, "right_wrist": 16, "left_hip": 23, "right_hip": 24,
              "left_knee": 25, "right_knee": 26, "left_ankle": 27, "right_ankle": 28}
IDX = np.array([MP_OF_COCO[k] for k in COCO_KEYPOINTS])
VIEW = {"kneeup": "C", "sidecrunch": "C", "pushup": "A"}


def frames(lm: np.ndarray, dt: float, fps: float):
    """(32,33,4) -> (t, Pose|None) 를 fps로 보간해 낸다."""
    xy = lm[:, IDX, :2].astype(float)
    conf = np.nan_to_num(lm[:, IDX, 3].astype(float), nan=0.0)
    ok = np.isfinite(xy).all(axis=2)
    conf[~ok] = 0.0
    n = len(lm)
    for t in np.arange(0, (n - 1) * dt + 1e-9, 1 / fps):
        f = t / dt
        i = min(int(f), n - 2)
        w = f - i
        a, b = xy[i], xy[i + 1]
        p = np.where(ok[i][:, None] & ok[i + 1][:, None], (1 - w) * a + w * b,
                     np.where(ok[i][:, None], a, b))
        c = np.minimum(conf[i], conf[i + 1]) if 0 < w else conf[i]
        if not (ok[i] | ok[i + 1]).any():
            yield t, None
            continue
        yield t, Pose(np.nan_to_num(p), c)


def run(ex: str, mp_dir: Path, labels: Path, dt: float, fps: float) -> dict:
    spec = EXERCISES[ex]
    cfg = load_exercise_config(ex)
    meta = pd.read_csv(labels / f"{ex}_{VIEW[ex]}.csv", dtype={"type": str}).set_index(["day", "clip"])
    cond_of = {code: cond for code, cond, *_ in spec["checks"]}
    if "depth_from" in spec:
        cond_of[cfg.depth.code] = spec["depth_from"][0]
    enabled = [c.code for c in cfg.form_checks if c.enabled] + ([cfg.depth.code] if "depth_from" in spec else [])
    reps, clips = [], []
    for f in sorted((mp_dir / f"{ex}_{VIEW[ex]}").glob("*.npz")):
        if f.name.endswith(".tmp.npz"):
            continue
        day = f.stem.split("_", 1)[1]
        z = np.load(f)
        for clip, lm in zip(z["clips"], z["lm"]):
            key = (day, str(clip))
            if key not in meta.index:
                continue
            m = meta.loc[key]
            if m["type"] in spec.get("exclude_types", []):
                continue
            an = Analyzer(cfg)
            n_frames = live_frames = 0
            live_codes = defaultdict(int)
            for t, pose in frames(lm, dt, fps):
                r = an.process(pose, t)
                n_frames += 1
                if r.live_feedback:
                    live_frames += 1
                    for c in r.live_feedback:
                        live_codes[c] += 1
                if r.rep:
                    reps.append({"day": day, "clip": str(clip), "type": m["type"], "score": r.rep.score,
                                 "dur": r.rep.summary.duration, **{c: int(c in r.rep.feedback) for c in enabled},
                                 **{f"bad_{c}": int(m[cond_of[c]] == 0) for c in enabled}})
            clips.append({"day": day, "clip": str(clip), "type": m["type"], "reps": an.counter.reps,
                          "live_frac": live_frames / max(n_frames, 1), **{f"live_{c}": live_codes[c] / max(n_frames, 1) for c in enabled},
                          "all_ok": int(all(m[c] == 1 for c in cond_of.values())),
                          **{f"bad_{c}": int(m[cond_of[c]] == 0) for c in enabled}})
    return {"reps": pd.DataFrame(reps), "clips": pd.DataFrame(clips), "enabled": enabled}


def report(ex: str, dt: float, res: dict) -> None:
    R, C, enabled = res["reps"], res["clips"], res["enabled"]
    print(f"\n## {ex}  (사진 간격 {dt}s 가정)")
    zero = (C["reps"] == 0).mean()
    print(f"클립 {len(C)}개, 앱이 센 회 {len(R)}회 (클립당 {C['reps'].mean():.1f}), 한 번도 못 센 클립 {zero:.0%}")
    if len(R):
        print(f"1회 걸린 시간 중앙값 {R['dur'].median():.2f}s")
    ok = C[C["all_ok"] == 1]
    okr = R.merge(ok[["day", "clip"]], on=["day", "clip"]) if len(R) else R
    if len(okr):
        print(f"모든 조건 정상인 클립 {len(ok)}개: 평균 점수 {okr['score'].mean():.1f}, 100점 비율 {(okr['score'] == 100).mean():.0%}, "
              f"점수 분포 {okr['score'].value_counts().sort_index(ascending=False).to_dict()}")
        print(f"  정상 클립에서 실시간 경고가 뜬 프레임 비율 평균 {ok['live_frac'].mean():.1%}")
    print("| 조건 | 위반 회 잡음 | 정상 회 오경보 | 1회 BA | 위반 클립 중 0회 | 실시간 경고(위반/정상 클립 프레임) |")
    print("|---|---|---|---|---|---|")
    for c in enabled:
        if not len(R):
            break
        bad, good = R[R[f"bad_{c}"] == 1], R[R[f"bad_{c}"] == 0]
        tpr = bad[c].mean() if len(bad) else np.nan
        fpr = good[c].mean() if len(good) else np.nan
        cb, cg = C[C[f"bad_{c}"] == 1], C[C[f"bad_{c}"] == 0]
        print(f"| {c} | {tpr:.0%} ({len(bad)}회) | {fpr:.0%} ({len(good)}회) | {(tpr + 1 - fpr) / 2:.2f} | "
              f"{(cb['reps'] == 0).mean():.0%} | {cb[f'live_{c}'].mean():.1%} / {cg[f'live_{c}'].mean():.1%} |")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mp", required=True)
    ap.add_argument("--labels", required=True, help="mediapipe_features.py --out 폴더 (정오 정보)")
    ap.add_argument("--dt", type=float, nargs="+", default=[0.25], help="사진 사이 간격 가정 (초)")
    ap.add_argument("--fps", type=float, default=15)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--save", help="회/클립 결과 CSV를 쓸 폴더")
    a = ap.parse_args()
    for dt in a.dt:
        for ex in EXERCISES:
            if a.only and ex not in a.only:
                continue
            res = run(ex, Path(a.mp), Path(a.labels), dt, a.fps)
            report(ex, dt, res)
            if a.save:
                Path(a.save).mkdir(parents=True, exist_ok=True)
                res["reps"].to_csv(Path(a.save) / f"{ex}_dt{dt}_reps.csv", index=False, encoding="utf-8-sig")
                res["clips"].to_csv(Path(a.save) / f"{ex}_dt{dt}_clips.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
