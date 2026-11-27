"""MediaPipe 좌표(클립당 32장)를 동작 1회 단위로 잘라 1회마다 특징을 요약한다.

앱(frontend/src/analysis.js)은 동작 1회가 끝날 때마다 그 회의 최솟값/최댓값으로 점수를 매기므로,
클립 단위가 아니라 1회 단위로 다시 평가하기 위한 스크립트다.

    python rep_features.py --mp mp --features out_mp --out out_rep
    python evaluate_conditions.py --csv out_rep/kneeup_C.csv --views R R+P --exclude-ref

1회 자르기 (클립마다 신호의 범위로 임계를 정한다. 앱의 고정 임계와 달리 얕은 동작도 1회로 잡기 위해서다):
    니업, 사이드 크런치  신호 = 무릎 높이(knee_h, 큰 쪽). 봉우리 = 위쪽 임계를 넘은 구간의 최댓값
    푸시업              신호 = -팔꿈치 각도(elbow_mean). 봉우리 = 가장 굽은 순간
    위쪽 임계 = 최솟값 + 0.6 x 범위, 아래쪽 임계 = 최솟값 + 0.3 x 범위. 범위가 너무 작으면(니업 0.15 몸통, 푸시업 15도) 동작 없음.
    1회 구간 = 앞 골짜기 ~ 다음 골짜기 (다리를 내려놓는 순간도 포함)
주의: 32장이 띄엄띄엄 찍혀서 1회가 4~5장뿐이다. 앱(초당 15~30장)보다 가장 깊은 순간을 놓치기 쉽다.

열:
    rep, n_reps, rep_frames       몇 번째 회 / 그 클립의 회 수 / 그 회의 프레임 수
    R_<특징>_min/_max/_med/_mean   1회 요약 (특징 이름은 extract_standing.py, extract_pushup.py와 같다)
    P_<특징>_...                   R_ - 같은 사람 정자세 클립의 **첫 번째 회** 값 (앱의 "기준 동작 1회")
    is_ref                        기준 클립 여부 (--exclude-ref 로 평가에서 뺀다)
    reach_count                   푸시업만: 그 회 팔꿈치 최솟값이 앱 설정 count_threshold(120도) 이하인지
                                  (0이면 지금 앱은 이 회를 1회로 세지 않는다)
"""
from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

import extract_pushup as pu
import extract_standing as st
from mediapipe_features import to_pts

REF_TYPE = {"kneeup": "033", "sidecrunch": "001", "pushup": "561"}
MIN_RANGE = {"kneeup": 0.15, "sidecrunch": 0.15, "pushup": 15.0}
APP_COUNT_THRESHOLD = 120.0  # analyzer/aipt_analyzer/configs/pushup.json


def frame_table(lm: np.ndarray, ex: str, min_conf: float) -> pd.DataFrame:
    pts = [to_pts(fr, min_conf) for fr in lm]
    if ex == "pushup":
        return pd.DataFrame([pu.frame_features(p) for p in pts])
    return pd.DataFrame([st.frame_features(st.load_pts(p, 2), st.UP_2D) for p in pts])


def segment(sig: np.ndarray, min_range: float) -> list[tuple[int, int]]:
    """봉우리마다 (시작, 끝) 프레임 번호(끝 포함). 신호는 클수록 동작 중."""
    s = pd.Series(sig).interpolate(limit_direction="both").to_numpy()
    if not np.isfinite(s).any():
        return []
    lo, hi = np.nanmin(s), np.nanmax(s)
    if hi - lo < min_range:
        return []
    up, down = lo + 0.6 * (hi - lo), lo + 0.3 * (hi - lo)
    peaks, state, best = [], "low", None
    for i, v in enumerate(s):
        if state == "low" and v >= up:
            state, best = "high", i
        elif state == "high":
            if v > s[best]:
                best = i
            if v <= down:
                peaks.append(best)
                state = "low"
    if state == "high":
        peaks.append(best)
    reps = []
    for k, p in enumerate(peaks):
        a = 0 if k == 0 else peaks[k - 1] + int(np.argmin(s[peaks[k - 1]:p + 1]))
        b = len(s) - 1 if k == len(peaks) - 1 else p + int(np.argmin(s[p:peaks[k + 1] + 1]))
        reps.append((a, b))
    return reps


def summarize(ft: pd.DataFrame) -> dict:
    out = {}
    for c in ft.columns:
        v = ft[c].to_numpy(dtype=float)
        v = v[np.isfinite(v)]
        out[f"R_{c}_min"] = float(v.min()) if v.size else np.nan
        out[f"R_{c}_max"] = float(v.max()) if v.size else np.nan
        out[f"R_{c}_med"] = float(np.median(v)) if v.size else np.nan
        out[f"R_{c}_mean"] = float(v.mean()) if v.size else np.nan  # 앱(RepCounter)은 중앙값 대신 평균을 쓴다
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mp", required=True)
    ap.add_argument("--features", required=True, help="mediapipe_features.py --out 폴더 (정오 정보를 가져온다)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-conf", type=float, default=0.3)
    a = ap.parse_args()
    warnings.simplefilter("ignore", RuntimeWarning)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for sub in sorted(Path(a.mp).iterdir()):
        src = Path(a.features) / f"{sub.name}.csv"
        if not sub.is_dir() or not src.exists():
            continue
        ex = sub.name.rsplit("_", 1)[0]
        meta = pd.read_csv(src, dtype={"type": str})
        keep = ["split", "day", "clip", "type", "desc"] + [c for c in meta.columns if c.startswith("ok_")]
        meta = meta[keep].set_index(["day", "clip"])
        rows = []
        for f in sorted(sub.glob("*.npz")):
            if f.name.endswith(".tmp.npz"):
                continue
            day = f.stem.split("_", 1)[1]
            z = np.load(f)
            for clip, lm in zip(z["clips"], z["lm"]):
                key = (day, str(clip))
                if key not in meta.index:
                    continue
                ft = frame_table(lm, ex, a.min_conf)
                sig = -ft["elbow_mean"].to_numpy(float) if ex == "pushup" else ft["knee_h"].to_numpy(float)
                reps = segment(sig, MIN_RANGE[ex])
                for k, (s0, s1) in enumerate(reps):
                    r = {"day": day, "clip": str(clip), **meta.loc[key].to_dict(),
                         "rep": k + 1, "n_reps": len(reps), "rep_frames": s1 - s0 + 1}
                    r |= summarize(ft.iloc[s0:s1 + 1])
                    if ex == "pushup":
                        r["reach_count"] = int(r["R_elbow_mean_min"] <= APP_COUNT_THRESHOLD)
                    rows.append(r)
        df = pd.DataFrame(rows)
        df["person"] = df["day"] + "/" + df["clip"].str.split("-").str[1]
        df["is_ref"] = (df["type"] == REF_TYPE[ex]).astype(int)
        base = [c for c in df.columns if c.startswith("R_")]
        ref = df[(df["is_ref"] == 1) & (df["rep"] == 1)].set_index("person")[base]
        ref = ref[~ref.index.duplicated()]
        P = df[base].to_numpy() - ref.reindex(df["person"]).to_numpy()
        df = pd.concat([df, pd.DataFrame(P, columns=["P_" + c[2:] for c in base], index=df.index)], axis=1)
        dst = out / src.name
        df.to_csv(dst, index=False, encoding="utf-8-sig")
        n_clip = df.groupby(["day", "clip"]).ngroups
        print(f"완료 {sub.name}: 클립 {n_clip}/{len(meta)}개에서 {len(df)}회, 클립당 회 수 "
              f"{df.groupby(['day', 'clip'])['n_reps'].first().value_counts().sort_index().to_dict()}, "
              f"1회 프레임 수 중앙값 {df['rep_frames'].median():.0f}"
              + (f", 앱 기준(120도)에 닿는 회 {df['reach_count'].mean():.0%}" if ex == "pushup" else "")
              + f" -> {dst}")


if __name__ == "__main__":
    main()
