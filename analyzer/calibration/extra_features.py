"""약한 조건 개선용 추가 특징: 시간 특징(T_)과 사람별 정규화(P_, Q_).

    python extra_features.py --mp mp --features out_mp --out out_extra

입력: mediapipe_extract.py 결과(npz)와 mediapipe_features.py 결과 CSV(<exercise>_<view>.csv).
출력: 같은 이름의 CSV에 열을 더해 --out 에 저장한다. evaluate_conditions.py --views T P Q 로 평가한다.

사람: 클립 이름 D33-1-561 의 가운데 숫자가 그날 촬영한 사람 번호다 (한 사람이 모든 자세 유형을 한 번씩 찍음).
열 접두어:
    T_   MediaPipe 32장 시계열에서 뽑은 시간 특징 (아래)
    P_   (M_, T_ 특징) - 같은 사람의 정자세 클립 값. 앱에서 "처음에 정자세로 기준 동작을 한 번 하기"를 흉내 낸다.
         정자세 클립(기준) 자신은 P_ 값이 0이 되므로 평가에서 빼야 한다 -> is_ref 열 (evaluate_conditions.py --exclude-ref).
    Q_   (M_, T_ 특징) - 같은 사람 전체 클립의 중앙값. 라벨 없이 그 사람의 평소 값을 기준으로 삼는 경우.

시간 특징 (프레임 간격은 1/15초로 가정, 길이는 클립의 몸통 길이 중앙값으로 나눔, 3프레임 중앙값으로 흔들림 제거):
  서 있는 운동 (C뷰 정면, 이미지 y는 아래쪽)
    ank_down_v_max   발목이 내려오는 최대 속도 (몸통/프레임). 터벅터벅이면 큼
    ank_up_v_max     발목이 올라가는 최대 속도
    ank_v_ratio_max  내려오는 속도 / 올라가는 속도
    ank_acc_max      발목 가속도(2차 차분) 최댓값. 착지 충격
    knee_down_v_max  무릎이 내려오는 최대 속도
    hip_bob_max      골반 중점의 위아래 흔들림 범위 / 몸통
    hip_drop_v_max   골반 중점이 내려가는 최대 속도
    ground_frac_med  두 발목이 모두 바닥 근처(들림 < 0.05 몸통)인 프레임 비율
    jerk_med         발목 높이 2차 차분 절댓값 평균 (움직임이 거친 정도)
  푸시업 (A뷰)
    sh_drop_max      어깨 중점의 위아래 이동 범위 / 어깨 폭 (가슴이 얼마나 내려가는지)
    nose_drop_max    코의 위아래 이동 범위 / 어깨 폭
    sw_ratio_min     손목-어깨 수직 거리 최솟값 / 최댓값 (작을수록 깊이 내려감, 길이 단위와 무관)
    elbow_range_max  팔꿈치 각도 최댓값 - 최솟값
    elbow_p10_min    팔꿈치 각도 10퍼센타일 (최솟값보다 튐에 강함)
주의: 32장은 연속 촬영이 아니라 띄엄띄엄 찍힌 것이라(다리 한 번 올렸다 내리는 데 4~5장) 속도/가속도 특징은 약하다.
이름 끝(_min/_max/_med)은 evaluate_conditions.py가 특징 열을 고르는 규칙에 맞춘 것이다.
"""
from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

REF_TYPE = {"kneeup": "033", "sidecrunch": "001", "pushup": "561"}
I = {"nose": 0, "lsh": 11, "rsh": 12, "lel": 13, "rel": 14, "lwr": 15, "rwr": 16,
     "lhip": 23, "rhip": 24, "lkn": 25, "rkn": 26, "lan": 27, "ran": 28}


def _clean(lm: np.ndarray, min_conf: float) -> np.ndarray:
    """(32,33,4) -> (32,33,2), visibility가 낮은 점은 NaN, 시간축 3프레임 중앙값."""
    xy = lm[..., :2].copy()
    xy[lm[..., 3] < min_conf] = np.nan
    pad = np.concatenate([xy[:1], xy, xy[-1:]])
    stack = np.stack([pad[:-2], pad[1:-1], pad[2:]])
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return np.nanmedian(stack, axis=0)


def _nan(f):
    import warnings

    def g(*a):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            try:
                v = float(f(*a))
            except ValueError:
                return np.nan
            return v if np.isfinite(v) else np.nan
    return g


@_nan
def _max(x):
    return np.nanmax(x)


@_nan
def _mean(x):
    return np.nanmean(x)


def standing_features(lm: np.ndarray, min_conf: float) -> dict:
    p = _clean(lm, min_conf)
    S = (p[:, I["lsh"]] + p[:, I["rsh"]]) / 2
    H = (p[:, I["lhip"]] + p[:, I["rhip"]]) / 2
    torso = np.nanmedian(np.linalg.norm(S - H, axis=1))
    if not np.isfinite(torso) or torso <= 0:
        return {}
    ay = np.stack([p[:, I["lan"]][:, 1], p[:, I["ran"]][:, 1]], 1)   # (32,2) 아래쪽 +
    ky = np.stack([p[:, I["lkn"]][:, 1], p[:, I["rkn"]][:, 1]], 1)
    lift = (np.nanmax(ay, axis=0, keepdims=True) - ay) / torso if np.isfinite(ay).any() else ay
    v_ank = np.diff(ay, axis=0) / torso          # + = 내려옴
    v_knee = np.diff(ky, axis=0) / torso
    acc = np.diff(ay, n=2, axis=0) / torso
    hy = H[:, 1] / torso
    f = {
        "ank_down_v_max": _max(v_ank),
        "ank_up_v_max": _max(-v_ank),
        "ank_acc_max": _max(np.abs(acc)),
        "knee_down_v_max": _max(v_knee),
        "hip_bob_max": _max(hy) - (-_max(-hy)),
        "hip_drop_v_max": _max(np.diff(hy)),
        "ground_frac_med": _mean((lift < 0.05).all(axis=1)) if np.isfinite(lift).all() else _mean(np.nanmax(lift, axis=1) < 0.05),
        "jerk_med": _mean(np.abs(acc)),
    }
    f["ank_v_ratio_max"] = f["ank_down_v_max"] / f["ank_up_v_max"] if f["ank_up_v_max"] and f["ank_up_v_max"] > 0 else np.nan
    return f


def _angle(a, b, c):
    v1, v2 = a - b, c - b
    n = np.linalg.norm(v1, axis=-1) * np.linalg.norm(v2, axis=-1)
    with np.errstate(all="ignore"):
        return np.degrees(np.arccos(np.clip((v1 * v2).sum(-1) / n, -1, 1)))


def pushup_features(lm: np.ndarray, min_conf: float) -> dict:
    p = _clean(lm, min_conf)
    sw = np.nanmedian(np.linalg.norm(p[:, I["lsh"]] - p[:, I["rsh"]], axis=1))
    if not np.isfinite(sw) or sw <= 0:
        return {}
    S = (p[:, I["lsh"]] + p[:, I["rsh"]]) / 2
    W = (p[:, I["lwr"]] + p[:, I["rwr"]]) / 2
    d = np.abs(W[:, 1] - S[:, 1])
    el = np.concatenate([_angle(p[:, I["lsh"]], p[:, I["lel"]], p[:, I["lwr"]]),
                         _angle(p[:, I["rsh"]], p[:, I["rel"]], p[:, I["rwr"]])])
    sy, ny = S[:, 1] / sw, p[:, I["nose"], 1] / sw
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return {
            "sh_drop_max": _max(sy) + _max(-sy),
            "nose_drop_max": _max(ny) + _max(-ny),
            "sw_ratio_min": float(np.nanmin(d) / np.nanmax(d)) if np.isfinite(d).sum() > 3 else np.nan,
            "elbow_range_max": _max(el) + _max(-el),
            "elbow_p10_min": float(np.nanpercentile(el, 10)) if np.isfinite(el).sum() > 3 else np.nan,
        }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mp", required=True)
    ap.add_argument("--features", required=True, help="mediapipe_features.py --out 폴더")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-conf", type=float, default=0.3)
    a = ap.parse_args()
    warnings.simplefilter("ignore", RuntimeWarning)  # 사람이 안 잡힌 프레임(전부 NaN)에서 나는 경고
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for sub in sorted(Path(a.mp).iterdir()):
        src = Path(a.features) / f"{sub.name}.csv"
        if not sub.is_dir() or not src.exists():
            continue
        ex = sub.name.rsplit("_", 1)[0]
        fn = pushup_features if ex == "pushup" else standing_features
        tf = {}
        for f in sorted(sub.glob("*.npz")):
            if f.name.endswith(".tmp.npz"):
                continue
            day = f.stem.split("_", 1)[1]
            z = np.load(f)
            for clip, lm in zip(z["clips"], z["lm"]):
                tf[(day, str(clip))] = fn(lm, a.min_conf)
        df = pd.read_csv(src, dtype={"type": str})
        T = pd.DataFrame([tf.get((d, c), {}) for d, c in zip(df["day"], df["clip"])], index=df.index)
        df = pd.concat([df, T.add_prefix("T_")], axis=1)
        df["person"] = df["day"] + "/" + df["clip"].str.split("-").str[1]
        df["is_ref"] = (df["type"] == REF_TYPE[ex]).astype(int)
        base = [c for c in df.columns if (c.startswith("M_") or c.startswith("T_")) and c[-4:] in ("_min", "_max", "_med")]
        ref = df[df["is_ref"] == 1].groupby("person")[base].mean()
        med = df.groupby("person")[base].transform("median")
        P = df[base].to_numpy() - ref.reindex(df["person"]).to_numpy()
        Q = df[base].to_numpy() - med.to_numpy()
        df = pd.concat([df, pd.DataFrame(P, columns=["P_" + c[2:] for c in base], index=df.index),
                        pd.DataFrame(Q, columns=["Q_" + c[2:] for c in base], index=df.index)], axis=1)
        dst = out / src.name
        df.to_csv(dst, index=False, encoding="utf-8-sig")
        print(f"완료 {sub.name}: {len(df)}개 구간, 사람 {df['person'].nunique()}명, 기준 클립 없는 사람 "
              f"{df['person'].nunique() - len(ref)}명 -> {dst}")


if __name__ == "__main__":
    main()
