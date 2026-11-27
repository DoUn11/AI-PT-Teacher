"""푸시업 2D 라벨에서 팔·어깨 각도를 구간(16프레임) 단위로 요약해 CSV로 저장한다.

numpy만 있으면 돌아간다 (analyzer 패키지에 의존하지 않는다).

사용 예:
    python extract_pushup.py --root "D:/데이터/AI_PT_선택운동" --out out
    python extract_pushup.py --root ... --out out --max-seconds 150   # 시간 제한. 다시 실행하면 이어서 한다

날짜(촬영일) 폴더마다 CSV 하나를 만든다 (out/<split>_<day>.csv). 이미 있으면 건너뛴다.
뷰: A=view1(정면에 가장 가까움), C=view3(측면, 비교용).

구간 요약 특징(뷰마다):
    elbow_mean/L/R     어깨-팔꿈치-손목 각도 (팔꿈치 굽힘)
    shoulder_mean/L/R  골반-어깨-팔꿈치 각도 (팔이 몸통에서 벌어진 정도)
    hand_ratio         손목 사이 거리 / 어깨 사이 거리 (손 위치)
    각각 _min, _max, _med
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

VIEWS = {"A": "view1", "C": "view3"}

# 라벨의 틀린 조건 이름 -> CSV 열 이름 (True면 그 조건은 정상)
CONDITIONS = {
    "척추의 중립": "ok_spine",
    "이완시 팔꿈치 90도": "ok_elbow90",
    "가슴의 충분한 이동": "ok_chest",
    "손의 위치 가슴 중앙 여부": "ok_hand",
    "고개 젖힘/숙임 여부": "ok_head",
}

JOINTS = ["Shoulder", "Elbow", "Wrist", "Hip"]
FEATURES = ["elbow_mean", "elbow_L", "elbow_R", "shoulder_mean", "shoulder_L", "shoulder_R", "hand_ratio"]


def _pt(pts: dict, name: str) -> np.ndarray:
    p = pts.get(name)
    if not p or p.get("x") is None or p.get("y") is None:
        return np.full(2, np.nan)
    return np.array([float(p["x"]), float(p["y"])])


def angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """b를 꼭짓점으로 하는 a-b-c 각도(도). 점이 비었거나 길이가 0이면 NaN."""
    v1, v2 = a - b, c - b
    n = np.linalg.norm(v1) * np.linalg.norm(v2)
    if not np.isfinite(n) or n == 0:
        return math.nan
    return math.degrees(math.acos(float(np.clip(np.dot(v1, v2) / n, -1.0, 1.0))))


def frame_features(pts: dict) -> dict[str, float]:
    p = {f"{s}{j}": _pt(pts, f"{side} {j}") for side, s in (("Left", "L"), ("Right", "R")) for j in JOINTS}
    f: dict[str, float] = {}
    for s in ("L", "R"):
        f[f"elbow_{s}"] = angle(p[f"{s}Shoulder"], p[f"{s}Elbow"], p[f"{s}Wrist"])
        f[f"shoulder_{s}"] = angle(p[f"{s}Hip"], p[f"{s}Shoulder"], p[f"{s}Elbow"])
    f["elbow_mean"] = _nanmean(f["elbow_L"], f["elbow_R"])
    f["shoulder_mean"] = _nanmean(f["shoulder_L"], f["shoulder_R"])
    sh = np.linalg.norm(p["LShoulder"] - p["RShoulder"])
    wr = np.linalg.norm(p["LWrist"] - p["RWrist"])
    f["hand_ratio"] = float(wr / sh) if np.isfinite(sh) and sh > 0 and np.isfinite(wr) else math.nan
    return f


def _nanmean(*xs: float) -> float:
    v = [x for x in xs if not math.isnan(x)]
    return sum(v) / len(v) if v else math.nan


def summarize(rows: list[dict[str, float]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for name in FEATURES:
        v = np.array([r[name] for r in rows], dtype=float)
        v = v[np.isfinite(v)]
        out[f"{name}_min"] = float(v.min()) if v.size else math.nan
        out[f"{name}_max"] = float(v.max()) if v.size else math.nan
        out[f"{name}_med"] = float(np.median(v)) if v.size else math.nan
    out["n_valid"] = float(sum(np.isfinite(r["elbow_mean"]) for r in rows))
    return out


def clip_row(path: Path, split: str, day: str) -> dict:
    j = json.loads(path.read_text(encoding="utf-8"))
    info = j["type_info"]
    row: dict = {
        "split": split, "day": day, "clip": path.stem,
        "type": info["key"], "desc": str(info.get("description", "")).strip(),
        "n_frames": len(j["frames"]),
    }
    cond = {c["condition"]: bool(c["value"]) for c in info["conditions"]}
    for k, col in CONDITIONS.items():
        row[col] = int(cond[k]) if k in cond else ""
    for vname, vkey in VIEWS.items():
        per = [frame_features(fr[vkey]["pts"]) for fr in j["frames"] if vkey in fr]
        for k, val in summarize(per).items():
            row[f"{vname}_{k}"] = round(val, 3) if not math.isnan(val) else ""
    return row


def process_day(root: Path, split: str, day: str, out_dir: Path) -> int:
    label_dir = root / split / "푸시업" / "labels" / day
    files = sorted(p for p in label_dir.glob("*.json") if not p.name.endswith("-3d.json"))
    rows = []
    for p in files:
        try:
            rows.append(clip_row(p, split, day))
        except Exception as e:  # 깨진 라벨 하나가 전체를 멈추지 않게
            print(f"  건너뜀 {p.name}: {e}", file=sys.stderr)
    if rows:
        tmp = out_dir / f"{split}_{day}.csv.tmp"
        with tmp.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        tmp.replace(out_dir / f"{split}_{day}.csv")
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="AI_PT_선택운동 폴더")
    ap.add_argument("--out", required=True, help="CSV를 저장할 폴더")
    ap.add_argument("--max-seconds", type=float, default=0, help="이 시간이 지나면 멈춘다 (0이면 제한 없음)")
    ap.add_argument("--force", action="store_true", help="이미 만든 날짜도 다시 처리")
    a = ap.parse_args()

    root, out_dir = Path(a.root), Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    todo = [(s, d.name) for s in ("Training", "Validation")
            if (root / s / "푸시업" / "labels").is_dir()
            for d in sorted((root / s / "푸시업" / "labels").iterdir()) if d.is_dir()]
    for split, day in todo:
        if (out_dir / f"{split}_{day}.csv").exists() and not a.force:
            print(f"있음 {split}/{day}")
            continue
        if a.max_seconds and time.time() - t0 > a.max_seconds:
            print("시간 제한: 남은 날짜는 다시 실행해서 이어서 처리하세요")
            break
        t = time.time()
        n = process_day(root, split, day, out_dir)
        print(f"완료 {split}/{day}: {n}개 구간, {time.time() - t:.1f}s")


if __name__ == "__main__":
    main()
