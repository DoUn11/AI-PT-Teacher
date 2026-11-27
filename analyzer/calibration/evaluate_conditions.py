"""구간 요약 CSV에서 틀린 조건(ok_* == 0)을 얼마나 잘 가려내는지 조건별로 평가한다.

    python evaluate_conditions.py --csv out_standing/kneeup.csv
    python evaluate_conditions.py --csv out_standing/sidecrunch.csv --exclude-types 028
    python evaluate_conditions.py --csv out/*.csv --views A C --conditions ok_elbow90 ok_chest ok_hand         --test-day Day32_201104_F

평가는 촬영일(day) 단위로 하루씩 빼고 검증(leave-one-day-out)한 균형정확도(BA, 0.5=찍기)의 평균이다.
    best1   특징 하나를 고정하고 임계값/방향만 학습 날짜에서 정한다. 특징을 결과를 보고 고르므로 약간 낙관적이다.
    nested  학습 날짜에서 특징까지 고른다 (가장 정직한 "특징 1개" 성능).
    multi   그 뷰의 모든 특징으로 로지스틱 회귀 (표준화, 중앙값 대치, class_weight=balanced).
    AUC     best1 특징의 전체 데이터 AUC (방향 맞춤).
--test-day 를 주면 그날은 학습/검증에서 빼고 최종 시험 점수를 따로 낸다.

결과 표는 표준출력, 특징별 상세는 --report CSV로 저장한다.
"""
from __future__ import annotations

import argparse
import math
import sys

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


def fit_threshold(x: np.ndarray, y: np.ndarray) -> tuple[float, int, float]:
    """y=1(위반)을 가르는 임계값. 반환 (t, sign, BA): sign*x > sign*t 이면 위반으로 판정."""
    ok = np.isfinite(x)
    x, y = x[ok], y[ok]
    npos, nneg = y.sum(), (1 - y).sum()
    if npos == 0 or nneg == 0:
        return math.nan, 1, math.nan
    order = np.argsort(x)
    xs, ys = x[order], y[order]
    # 컷 i: 앞의 i개는 "작다" 쪽. 같은 값 사이는 자르지 않는다.
    pos_le = np.concatenate([[0], np.cumsum(ys)])
    neg_le = np.concatenate([[0], np.cumsum(1 - ys)])
    valid = np.concatenate([[True], xs[1:] != xs[:-1], [True]])
    # sign=+1: 큰 쪽이 위반. TPR = (npos - pos_le)/npos, TNR = neg_le/nneg
    ba_hi = 0.5 * ((npos - pos_le) / npos + neg_le / nneg)
    ba_lo = 0.5 * (pos_le / npos + (nneg - neg_le) / nneg)
    ba_hi[~valid], ba_lo[~valid] = -1, -1
    i_hi, i_lo = int(np.argmax(ba_hi)), int(np.argmax(ba_lo))
    mids = np.concatenate([[xs[0] - 1e-9], (xs[1:] + xs[:-1]) / 2, [xs[-1] + 1e-9]])
    if ba_hi[i_hi] >= ba_lo[i_lo]:
        return float(mids[i_hi]), 1, float(ba_hi[i_hi])
    return float(mids[i_lo]), -1, float(ba_lo[i_lo])


def predict_threshold(x: np.ndarray, t: float, sign: int, fill: int) -> np.ndarray:
    pred = (sign * x > sign * t).astype(int)
    pred[~np.isfinite(x)] = fill
    return pred


def ba(y: np.ndarray, p: np.ndarray) -> float:
    if len(set(y)) < 2:
        return math.nan
    return float(balanced_accuracy_score(y, p))


def auc_oriented(x: np.ndarray, y: np.ndarray) -> float:
    ok = np.isfinite(x)
    if len(set(y[ok])) < 2:
        return math.nan
    a = roc_auc_score(y[ok], x[ok])
    return max(a, 1 - a)


def evaluate(df: pd.DataFrame, cond: str, feats: list[str], test_day: str | None):
    y_all = (df[cond] == 0).astype(int).to_numpy()
    X_all = df[feats].to_numpy(dtype=float)
    days_all = df["day"].to_numpy()
    dev = days_all != test_day if test_day else np.ones(len(df), bool)
    X, y, days = X_all[dev], y_all[dev], days_all[dev]
    folds = sorted(set(days))

    # 특징 1개: 특징별로 날짜별 BA
    per_feat = np.full((len(feats), len(folds)), np.nan)
    nested, multi = [], []
    for fi, d in enumerate(folds):
        tr, te = days != d, days == d
        if len(set(y[te])) < 2:
            continue
        train_ba = []
        for k in range(len(feats)):
            t, s, b = fit_threshold(X[tr, k], y[tr])
            train_ba.append(b)
            if not math.isnan(t):
                per_feat[k, fi] = ba(y[te], predict_threshold(X[te, k], t, s, int(np.median(y[tr]))))
        kbest = int(np.nanargmax(train_ba))
        nested.append(per_feat[kbest, fi])
        m = make_model().fit(X[tr], y[tr])
        multi.append(ba(y[te], m.predict(X[te])))
    mean_feat = np.nanmean(per_feat, axis=1)
    order = np.argsort(-np.nan_to_num(mean_feat, nan=-1))
    k1 = int(order[0])
    t_all, s_all, _ = fit_threshold(X[:, k1], y)
    res = {
        "n": int(len(y)), "n_bad": int(y.sum()),
        "best1": feats[k1], "best1_BA": float(mean_feat[k1]),
        "best1_thr": t_all, "best1_dir": ">" if s_all > 0 else "<",
        "best1_AUC": auc_oriented(X[:, k1], y),
        "nested_BA": float(np.nanmean(nested)), "multi_BA": float(np.nanmean(multi)),
        "top": [(feats[k], float(mean_feat[k])) for k in order[:5]],
    }
    if test_day:
        te = ~dev
        m = make_model().fit(X, y)
        res["test_best1_BA"] = ba(y_all[te], predict_threshold(X_all[te, k1], t_all, s_all, 0))
        res["test_multi_BA"] = ba(y_all[te], m.predict(X_all[te]))
    # 정상(ok) 구간의 분포: 정답값/오차범위 후보
    xn = X[y == 0, k1]
    xn = xn[np.isfinite(xn)]
    res["ok_p05"], res["ok_p50"], res["ok_p95"] = (float(np.percentile(xn, q)) for q in (5, 50, 95))
    return res, {feats[k]: mean_feat[k] for k in range(len(feats))}


def make_model():
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                         LogisticRegression(C=0.3, class_weight="balanced", max_iter=2000))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, nargs="+", help="CSV 파일 (여러 개면 이어 붙인다)")
    ap.add_argument("--conditions", nargs="+", help="평가할 ok_* 열 (기본: 전부)")
    ap.add_argument("--views", nargs="+", default=["A", "C", "D3"], help="열 접두어. M+T 처럼 +로 묶으면 함께 쓴다")
    ap.add_argument("--exclude-types", nargs="*", default=[], help="평가에서 뺄 자세 유형 번호 (예: 028)")
    ap.add_argument("--test-day", help="최종 시험용으로 따로 둘 day 값")
    ap.add_argument("--exclude-ref", action="store_true",
                    help="is_ref==1(사람별 기준 정자세 클립) 행을 뺀다. extra_features.py의 P_ 특징을 평가할 때 사용")
    ap.add_argument("--report", help="특징별 BA를 저장할 CSV")
    a = ap.parse_args()

    df = pd.concat([pd.read_csv(f, dtype={"type": str}) for f in a.csv], ignore_index=True)
    if a.exclude_types:
        df = df[~df["type"].isin(a.exclude_types)]
    if a.exclude_ref:
        df = df[df["is_ref"] != 1]
    conds = a.conditions or [c for c in df.columns if c.startswith("ok_")]
    df = df.dropna(subset=conds)
    print(f"{', '.join(a.csv)}: {len(df)}개 구간, 날짜 {df['day'].nunique()}개, 조건 {conds}")
    detail = []
    for cond in conds:
        print(f"\n## {cond}  (위반 {int((df[cond] == 0).sum())} / {len(df)})")
        print("| 뷰 | best1 특징 | best1 BA | AUC | 임계(전체) | nested BA | multi BA | 정상 5/50/95% |"
              + (" 시험 best1/multi |" if a.test_day else ""))
        print("|---|---|---|---|---|---|---|---|" + ("---|" if a.test_day else ""))
        for v in a.views:
            pre = tuple(f"{x}_" for x in v.split("+"))  # "M+T" -> 두 접두어 특징을 함께 쓴다
            feats = [c for c in df.columns if c.startswith(pre) and c[-4:] in ("_min", "_max", "_med")]
            if not feats:
                continue
            r, per = evaluate(df, cond, feats, a.test_day)
            line = (f"| {v} | {r['best1'] if "+" in v else r['best1'][len(v) + 1:]} | {r['best1_BA']:.2f} | {r['best1_AUC']:.2f} | "
                    f"{r['best1_dir']} {r['best1_thr']:.3g} | {r['nested_BA']:.2f} | {r['multi_BA']:.2f} | "
                    f"{r['ok_p05']:.3g} / {r['ok_p50']:.3g} / {r['ok_p95']:.3g} |")
            if a.test_day:
                line += f" {r['test_best1_BA']:.2f} / {r['test_multi_BA']:.2f} |"
            print(line)
            print("|  | 상위: " + ", ".join(f"{n if '+' in v else n[len(v) + 1:]} {b:.2f}" for n, b in r["top"][1:4]) + " |||||||"
                  + ("|" if a.test_day else ""))
            detail += [{"cond": cond, "view": v, "feature": f, "lodo_BA": b} for f, b in per.items()]
    if a.report:
        pd.DataFrame(detail).to_csv(a.report, index=False, encoding="utf-8-sig")
        print(f"\n상세 -> {a.report}", file=sys.stderr)


if __name__ == "__main__":
    main()
