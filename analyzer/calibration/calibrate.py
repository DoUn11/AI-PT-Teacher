"""1회 단위 데이터(rep_features.py 결과)에서 조건별 임계값을 구해 운동 설정 JSON을 만든다.

    python calibrate.py --reps out_rep --out ../aipt_analyzer/configs
    # 프론트엔드는 같은 파일의 사본을 쓴다 (테스트가 같은지 검사): frontend/configs/ 에 복사

조건마다 (앱 특징, 1회 통계 min|max|mean, 등급)을 아래 EXERCISES에 정해 두고, 임계값과 방향은 데이터에서 정한다.
    limit      전체 회(사크 유형 028 제외)에서 균형정확도가 가장 높은 임계값 (evaluate_conditions.fit_threshold)
    direction  위반 쪽 (above: limit보다 크면 위반, below: 작으면 위반)
    full_at    medium/weak 등급만: 위반 회 값의 중앙값. 거기서 감점이 penalty 전부가 된다 (기준 근처는 조금만 깎음)
    penalty / live 는 등급으로 정한다 (TIER). 단 "도달해야 하는" 조건은 live=false. 숫자는 데이터가 아니라 정한 값이다 (HANDOFF.md).
enabled=False 조건(시선, 다리 긴장)도 임계값은 구해서 남겨 둔다. 나중에 다시 켤 수 있게.
결과 표(하루씩 빼고 검증한 1회 BA 포함)를 표준출력으로 낸다.

앱 특징 이름 -> 1회 데이터 열 (정의는 같고 이름만 다르다. aipt_analyzer/features.py 참고):
    knee_gap = -knee_h,  elbow_angle_both = elbow_angle(서 있는 운동) / elbow_mean(푸시업),
    shoulder_angle_both = shoulder_mean,  elbow_angle(푸시업 rep 기준, 잘 보이는 쪽) ~ elbow_mean
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluate_conditions import ba, fit_threshold, predict_threshold

TIER = {"strong": {"penalty": 30, "live": True},
        "medium": {"penalty": 20, "live": True},
        "weak": {"penalty": 10, "live": False}}

# (code, 조건 열, 앱 특징, 통계, 등급, enabled)
EXERCISES = {
    "kneeup": {
        "csv": "kneeup_C.csv",
        "base": {"primary_feature": "knee_gap", "up_threshold": 0.68, "count_threshold": 0.58,
                 "min_rep_seconds": 0.3},
        "checks": [
            ("knee_low", "ok_kneeup", "ankle_lift", "max", "strong", True),
            ("spine_bend", "ok_spine", "torso_sw", "min", "medium", True),
            ("gaze_front", "ok_gaze", "head_yaw", "max", "weak", False),
            ("leg_tension", "ok_tension", "knee_gap", "min", "weak", False),
        ],
    },
    "sidecrunch": {
        "csv": "sidecrunch_C.csv",
        "exclude_types": ["028"],
        "base": {"primary_feature": "knee_gap", "up_threshold": 0.68, "count_threshold": 0.58,
                 "min_rep_seconds": 0.3},
        "checks": [
            ("hands_behind_head", "ok_hands", "elbow_angle_both", "mean", "strong", True),
            ("knee_side", "ok_side", "knee_lat", "max", "strong", True),
            ("elbow_knee_close", "ok_close", "knee_gap", "min", "medium", True),
            ("head_down", "ok_spine", "head_pitch", "min", "weak", True),
            ("gaze_front", "ok_gaze", "head_pitch", "max", "weak", False),
        ],
    },
    "pushup": {
        "csv": "pushup_A.csv",
        # count 145: 살짝만 내려간 깔짝 푸시업도 1회로 세고 shallow_depth로 감점한다 (2026-11-28 웹캠 테스트 후 결정)
        "base": {"primary_feature": "elbow_angle", "up_threshold": 150, "count_threshold": 145,
                 "min_rep_seconds": 0.6},
        "depth_from": ("ok_chest", "weak"),  # 가슴 이동(깔짝)은 depth 규칙으로 판정 (목표 각도는 데이터로)
        # 깔짝 푸시업은 약한 등급(최대 10점)보다 크게 깎는다: 1도당 2점, 최대 40점
        "depth_override": {"penalty_per_degree": 2.0, "min_score": 60},
        # 위 고정값을 _note 끝에 붙인다 (손으로 맞춘 pushup.json과 같게)
        "note_extra": "2026-11-28: count_threshold, depth 감점, hand_width는 데이터 없이 정한 고정값 (calibrate.py에 같이 반영).",
        # 손 위치(ok_hand, 골반-어깨-팔꿈치 각도)는 정면 웹캠에서 안 잡혀서 뺐다.
        # 손 간격(ok_elbow90 라벨 + hand_ratio)은 손을 넓게 짚어도 안 잡혀서 아래 고정 규칙(hand_out)으로 바꿨다.
        "checks": [],
        # 데이터로 정하지 않는 고정 조건. 푸시업 척추(몸 일직선)는 정면 카메라로는 판정하지 않기로 해서 꺼 둔다.
        "fixed_checks": [
            {"code": "hand_width", "feature": "hand_out", "stat": "mean", "limit": 0.25, "direction": "above",
             "full_at": 0.5, "penalty": 20, "tier": "medium", "live": True, "enabled": True,
             "_note": "어깨-손목 위치 고정 규칙 (데이터 라벨 없음). 웹캠으로 조정"},
            {"code": "body_line", "feature": "body_line", "stat": "min", "limit": 155, "penalty": 20,
             "enabled": False, "_note": "측면 촬영용 초기값. 정면 카메라에서는 끔 (HANDOFF.md)"},
        ],
    },
}

COLUMN = {"elbow_angle_both": "elbow_angle", "shoulder_angle_both": "shoulder_mean", "elbow_angle": "elbow_mean"}


def column(df: pd.DataFrame, ex: str, feature: str, stat: str) -> np.ndarray:
    """앱 특징/통계 -> 1회 데이터 값."""
    if feature == "knee_gap":  # -knee_h 이므로 min <-> max가 바뀐다
        flip = {"min": "max", "max": "min", "mean": "mean"}[stat]
        return -df[f"R_knee_h_{flip}"].to_numpy(float)
    name = COLUMN.get(feature, feature)
    if ex == "pushup" and feature == "elbow_angle_both":
        name = "elbow_mean"
    return df[f"R_{name}_{stat}"].to_numpy(float)


def lodo_ba(x: np.ndarray, y: np.ndarray, days: np.ndarray) -> float:
    out = []
    for d in sorted(set(days)):
        tr = days != d
        t, s, _ = fit_threshold(x[tr], y[tr])
        out.append(ba(y[~tr], predict_threshold(x[~tr], t, s, 0)))
    return float(np.nanmean(out))


def fit_rule(x: np.ndarray, y: np.ndarray, tier: str) -> dict:
    t, sign, _ = fit_threshold(x, y)
    rule = {"limit": round(float(t), 3), "direction": "above" if sign > 0 else "below"}
    bad = x[(y == 1) & np.isfinite(x)]
    if tier != "strong" and bad.size:
        fa = float(np.median(bad))
        if (fa > t) == (sign > 0) and fa != t:
            rule["full_at"] = round(fa, 3)
    return rule


def build(ex: str, spec: dict, reps: Path) -> tuple[dict, list[str]]:
    df = pd.read_csv(reps / spec["csv"], dtype={"type": str})
    if spec.get("exclude_types"):
        df = df[~df["type"].isin(spec["exclude_types"])]
    days = df["day"].to_numpy()
    report = []
    checks = []
    for code, cond, feature, stat, tier, enabled in spec["checks"]:
        x, y = column(df, ex, feature, stat), (df[cond] == 0).astype(int).to_numpy()
        rule = fit_rule(x, y, tier)
        # "도달해야 하는" 조건(최댓값이 limit에 못 미침, 최솟값이 limit을 못 넘김)은 동작 중 대부분의 프레임이
        # 아직 도달 전이라 실시간 경고가 계속 뜬다 -> 등급과 상관없이 반복이 끝난 뒤에만 판정한다.
        must_reach = (stat == "max" and rule["direction"] == "below") or (stat == "min" and rule["direction"] == "above")
        # 앱은 방향을 stat 기본값과 같으면 생략해도 되지만, 읽기 쉽게 항상 적는다
        checks.append({"code": code, "feature": feature, "stat": stat, **rule, "penalty": TIER[tier]["penalty"],
                       "tier": tier, "live": TIER[tier]["live"] and not must_reach, "enabled": enabled,
                       "_ba_rep": round(lodo_ba(x, y, days), 2)})
        report.append(f"| {ex} | {code} | {feature} {stat} {'>' if rule['direction'] == 'above' else '<'} {rule['limit']}"
                      f" | {rule.get('full_at', '-')} | {tier} | {'켬' if enabled else '끔'} | {lodo_ba(x, y, days):.2f} |")

    checks += spec.get("fixed_checks", [])
    depth = {"code": "knee_low", "target": spec["base"]["count_threshold"], "tolerance": 0,
             "penalty_per_degree": 0, "min_score": 100}  # 서 있는 운동: 깊이는 form_checks(knee_low)로 본다
    if "depth_from" in spec:
        cond, tier = spec["depth_from"]
        x, y = column(df, ex, "elbow_angle", "min"), (df[cond] == 0).astype(int).to_numpy()
        rule = fit_rule(x, y, tier)
        t, fa = rule["limit"], rule.get("full_at", rule["limit"] + 30)
        depth = {"code": "shallow_depth", "target": t, "tolerance": 0,
                 "penalty_per_degree": round(TIER[tier]["penalty"] / (fa - t), 3),
                 "min_score": 100 - TIER[tier]["penalty"]}
        depth.update(spec.get("depth_override", {}))
        report.append(f"| {ex} | shallow_depth (depth) | elbow_angle min > {t} | {fa} | {tier} | 켬 | {lodo_ba(x, y, days):.2f} |")

    cfg = {"exercise": ex, "version": 2, "calibrated": True,
           "_note": ("calibrate.py가 만든 파일 (AI Hub 데이터, MediaPipe lite 1회 단위). 웹캠 실측으로는 아직 검증 안 함. "
                     "_ba_rep = 하루씩 빼고 검증한 1회 균형정확도. 손으로 고치지 말고 calibrate.py를 고쳐 다시 만든다.")
                    + (" " + spec["note_extra"] if spec.get("note_extra") else ""),
           **spec["base"], "smoothing": 0.6, "min_keypoint_conf": 0.3, "score_step": 10,
           "depth": depth, "form_checks": checks, "judge": {"type": "threshold"}}
    return cfg, report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", required=True, help="rep_features.py --out 폴더")
    ap.add_argument("--out", required=True, help="설정 JSON을 쓸 폴더 (aipt_analyzer/configs)")
    ap.add_argument("--only", nargs="*", help="이 운동만")
    a = ap.parse_args()
    print("| 운동 | 코드 | 규칙 | full_at | 등급 | 사용 | 1회 BA |\n|---|---|---|---|---|---|---|")
    for ex, spec in EXERCISES.items():
        if a.only and ex not in a.only:
            continue
        cfg, report = build(ex, spec, Path(a.reps))
        print("\n".join(report))
        dst = Path(a.out) / f"{ex}.json"
        text = json.dumps(cfg, ensure_ascii=False, indent=2)
        dst.write_text(text.replace("\n", "\r\n") + "\r\n", encoding="utf-8")


if __name__ == "__main__":
    main()
