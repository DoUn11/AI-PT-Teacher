"""스탠딩 니업 / 스탠딩 사이드 크런치 라벨에서 전신 특징을 구간(16프레임) 단위로 요약해 CSV로 저장한다.

numpy만 있으면 돌아간다 (analyzer 패키지에 의존하지 않는다).

    python extract_standing.py --root "D:/데이터/AI_PT_선택운동" --out out_standing

운동마다 CSV 하나를 만든다 (out/kneeup.csv, out/sidecrunch.csv).

뷰: 서 있는 운동은 푸시업과 달리 **C=view3이 정면**(사람이 카메라를 마주 봄)이고 A=view1은 거의 측면이다.
A, C 2D 라벨과 3D 라벨(-3d.json, 단위 cm, y가 위쪽)을 모두 뽑아 비교한다. 열 접두어: A_, C_, D3_.
active가 "Yes"가 아닌 프레임은 빈 값으로 처리한다.

관절은 MediaPipe와 YOLO 공통인 것만 쓴다 (코, 귀, 어깨, 팔꿈치, 손목, 골반, 무릎, 발목).
목/등/허리는 어깨 중점(S), 골반 중점(H)으로 대체한다. 길이는 몸통 길이(|S-H|)로 나눠 정규화한다.

프레임 특징 (들어 올린 다리 = 무릎이 더 높은 쪽, 그쪽 팔/다리를 쓴다):
    head_pitch     (코 - 귀 중점)의 위쪽 성분 / 몸통   (위를 보면 커짐)
    head_yaw       (코 - 귀 중점)의 좌우(어깨선) 성분 / 몸통의 절댓값 (옆을 보면 커짐)
    nose_up        (코 - S)의 위쪽 성분 / 몸통        (숙이거나 고개를 떨구면 작아짐)
    neck_angle     H->S 와 S->코 사이 각도
    trunk_lean     H->S 가 수직에서 기운 각도
    torso_sw       몸통 / 어깨 폭 (정면에서 앞으로 숙이면 작아짐)
    knee_h         (무릎 - 같은 쪽 골반)의 위쪽 성분 / 몸통, 큰 쪽
    hip_flex       어깨-골반-무릎 각도 (들어 올린 쪽, 작을수록 높이 듦)
    knee_angle     골반-무릎-발목 각도 (들어 올린 쪽)
    ankle_lift     두 발목 높이 차 / 몸통
    knee_lat       들어 올린 무릎이 골반에서 바깥(골반선 방향)으로 벗어난 정도 / 몸통
    elbow_knee     들어 올린 쪽 팔꿈치-무릎 거리 / 몸통
    wrist_up       손목의 S 대비 높이 / 몸통 (두 손 중 낮은 쪽)
    wrist_head     손목-귀 중점 거리 / 몸통 (두 손 중 먼 쪽)
    elbow_angle    어깨-팔꿈치-손목 각도 (두 팔 평균)
    elbow_up       팔꿈치의 S 대비 높이 / 몸통 (두 팔 평균)
각 특징을 구간에서 _min, _max, _med 로 요약한다.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np

EXERCISES = {
    "kneeup": {
        "dir": "스탠딩 니업",
        "conditions": {
            "시선 정면": "ok_gaze",
            "척추 중립": "ok_spine",
            "무릎 충분히 올라오고": "ok_kneeup",
            "이완시 다리 긴장 유지": "ok_tension",
        },
    },
    "sidecrunch": {
        "dir": "스탠딩 사이드 크런치",
        "conditions": {
            "시선 정면 유지": "ok_gaze",
            "척추의 중립": "ok_spine",
            "수축시 무릎과 팔꿈치가 충분히 가까움": "ok_close",
            "무릎이 몸통 측면에서 올라오는지 여부": "ok_side",
            "양 손이 머리 뒤에 위치": "ok_hands",
        },
    },
}

VIEWS = {"A": "view1", "C": "view3"}
NAMES = {
    "nose": "Nose", "lear": "Left Ear", "rear": "Right Ear",
    "lsh": "Left Shoulder", "rsh": "Right Shoulder", "lel": "Left Elbow", "rel": "Right Elbow",
    "lwr": "Left Wrist", "rwr": "Right Wrist", "lhip": "Left Hip", "rhip": "Right Hip",
    "lkn": "Left Knee", "rkn": "Right Knee", "lan": "Left Ankle", "ran": "Right Ankle",
}
FEATURES = ["head_pitch", "head_yaw", "nose_up", "neck_angle", "trunk_lean", "torso_sw",
            "knee_h", "hip_flex", "knee_angle", "ankle_lift", "knee_lat", "elbow_knee",
            "wrist_up", "wrist_head", "elbow_angle", "elbow_up"]

UP_2D = np.array([0.0, -1.0])        # 이미지 좌표는 y가 아래쪽
UP_3D = np.array([0.0, 1.0, 0.0])    # 3D 라벨은 y가 위쪽


def load_pts(pts: dict, dim: int) -> dict[str, np.ndarray]:
    keys = "xyz"[:dim]
    out = {}
    for k, name in NAMES.items():
        p = pts.get(name)
        if not p or any(p.get(c) is None for c in keys):
            out[k] = np.full(dim, np.nan)
        else:
            out[k] = np.array([float(p[c]) for c in keys])
    return out


def angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """b를 꼭짓점으로 하는 a-b-c 각도(도)."""
    return vec_angle(a - b, c - b)


def vec_angle(v1: np.ndarray, v2: np.ndarray) -> float:
    n = np.linalg.norm(v1) * np.linalg.norm(v2)
    if not np.isfinite(n) or n == 0:
        return math.nan
    return math.degrees(math.acos(float(np.clip(np.dot(v1, v2) / n, -1.0, 1.0))))


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if np.isfinite(n) and n > 0 else np.full_like(v, np.nan)


def _nanmean(*xs: float) -> float:
    v = [x for x in xs if not math.isnan(x)]
    return sum(v) / len(v) if v else math.nan


def frame_features(p: dict[str, np.ndarray], up: np.ndarray) -> dict[str, float]:
    S = (p["lsh"] + p["rsh"]) / 2
    H = (p["lhip"] + p["rhip"]) / 2
    E = (p["lear"] + p["rear"]) / 2
    torso = float(np.linalg.norm(S - H))
    sw = float(np.linalg.norm(p["lsh"] - p["rsh"]))
    nan = math.nan
    if not np.isfinite(torso) or torso <= 0:
        return {k: nan for k in FEATURES}
    sh_axis = _unit(p["lsh"] - p["rsh"])
    h = lambda v: float(np.dot(v, up)) / torso  # noqa: E731  위쪽 성분 / 몸통

    f: dict[str, float] = {}
    f["head_pitch"] = h(p["nose"] - E)
    f["head_yaw"] = abs(float(np.dot(p["nose"] - E, sh_axis))) / torso
    f["nose_up"] = h(p["nose"] - S)
    f["neck_angle"] = vec_angle(S - H, p["nose"] - S)
    f["trunk_lean"] = vec_angle(S - H, up)
    f["torso_sw"] = torso / sw if np.isfinite(sw) and sw > 0 else nan

    side = {}
    for s, o in (("l", "r"), ("r", "l")):
        side[s] = {
            "knee_h": h(p[f"{s}kn"] - p[f"{s}hip"]),
            "hip_flex": angle(p[f"{s}sh"], p[f"{s}hip"], p[f"{s}kn"]),
            "knee_angle": angle(p[f"{s}hip"], p[f"{s}kn"], p[f"{s}an"]),
            "knee_lat": float(np.dot(p[f"{s}kn"] - p[f"{s}hip"], _unit(p[f"{s}hip"] - p[f"{o}hip"]))) / torso,
            "elbow_knee": float(np.linalg.norm(p[f"{s}el"] - p[f"{s}kn"])) / torso,
        }
    kl, kr = side["l"]["knee_h"], side["r"]["knee_h"]
    if math.isnan(kl) and math.isnan(kr):
        lifted = None
    else:
        lifted = "l" if (math.isnan(kr) or (not math.isnan(kl) and kl >= kr)) else "r"
    for k in ("knee_h", "hip_flex", "knee_angle", "knee_lat", "elbow_knee"):
        f[k] = side[lifted][k] if lifted else nan
    f["ankle_lift"] = abs(h(p["lan"] - p["ran"]))

    wu = [h(p[f"{s}wr"] - S) for s in "lr"]
    wh = [float(np.linalg.norm(p[f"{s}wr"] - E)) / torso for s in "lr"]
    f["wrist_up"] = min(wu) if not any(map(math.isnan, wu)) else nan
    f["wrist_head"] = max(wh) if not any(map(math.isnan, wh)) else nan
    f["elbow_angle"] = _nanmean(*(angle(p[f"{s}sh"], p[f"{s}el"], p[f"{s}wr"]) for s in "lr"))
    f["elbow_up"] = _nanmean(*(h(p[f"{s}el"] - S) for s in "lr"))
    return f


def summarize(rows: list[dict[str, float]], prefix: str) -> dict[str, float | str]:
    out: dict[str, float | str] = {}
    for name in FEATURES:
        v = np.array([r[name] for r in rows], dtype=float)
        v = v[np.isfinite(v)]
        for agg, fn in (("min", np.min), ("max", np.max), ("med", np.median)):
            out[f"{prefix}_{name}_{agg}"] = round(float(fn(v)), 4) if v.size else ""
    out[f"{prefix}_n_valid"] = int(sum(np.isfinite(r["knee_h"]) for r in rows))
    return out


def clip_row(path: Path, day: str, cond_map: dict[str, str]) -> dict:
    j = json.loads(path.read_text(encoding="utf-8"))
    info = j["type_info"]
    row: dict = {"day": day, "clip": path.stem, "type": info["key"],
                 "desc": str(info.get("description", "")).strip(), "n_frames": len(j["frames"])}
    cond = {c["condition"].strip(): bool(c["value"]) for c in info["conditions"]}
    unknown = set(cond) - set(cond_map)
    if unknown:
        raise ValueError(f"모르는 조건 {unknown}")
    for k, col in cond_map.items():
        row[col] = int(cond[k]) if k in cond else ""
    for vname, vkey in VIEWS.items():
        per = []
        for fr in j["frames"]:
            v = fr.get(vkey)
            if not v or v.get("active") != "Yes":
                continue
            per.append(frame_features(load_pts(v["pts"], 2), UP_2D))
        row |= summarize(per, vname)
    p3 = path.with_name(path.stem + "-3d.json")
    if p3.exists():
        j3 = json.loads(p3.read_text(encoding="utf-8"))
        per = [frame_features(load_pts(fr["pts"], 3), UP_3D) for fr in j3["frames"]]
        row |= summarize(per, "D3")
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="AI_PT_선택운동 폴더")
    ap.add_argument("--out", required=True, help="CSV를 저장할 폴더")
    ap.add_argument("--only", choices=list(EXERCISES), help="이 운동만 처리")
    a = ap.parse_args()
    root, out_dir = Path(a.root), Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for ex, cfg in EXERCISES.items():
        if a.only and ex != a.only:
            continue
        t0 = time.time()
        rows = []
        for split in ("Training", "Validation"):
            base = root / split / cfg["dir"] / "labels"
            if not base.is_dir():
                continue
            for day_dir in sorted(d for d in base.iterdir() if d.is_dir()):
                for p in sorted(day_dir.glob("*.json")):
                    if p.name.endswith("-3d.json"):
                        continue
                    try:
                        rows.append({"split": split} | clip_row(p, day_dir.name, cfg["conditions"]))
                    except Exception as e:  # 깨진 라벨 하나가 전체를 멈추지 않게
                        print(f"  건너뜀 {p.name}: {e}", file=sys.stderr)
        if not rows:
            continue
        fields = list(dict.fromkeys(k for r in rows for k in r))
        dst = out_dir / f"{ex}.csv"
        with dst.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, restval="")
            w.writeheader()
            w.writerows(rows)
        print(f"완료 {ex}: {len(rows)}개 구간 -> {dst} ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
