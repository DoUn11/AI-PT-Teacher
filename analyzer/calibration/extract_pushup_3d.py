"""푸시업 3D 라벨(-3d.json)에서 팔·어깨 각도를 구간 단위로 요약해 CSV로 저장한다.

3D 라벨에는 type_info가 없어서 틀린 조건(ok_*)은 파일명의 유형 번호(예: D33-1-561-3d -> 561)를
extract_pushup.py가 만든 2D CSV(out/*.csv)에서 찾아 붙인다. 같은 폴더의 extract_pushup.py를 먼저 돌려 둘 것.

    python extract_pushup_3d.py --root "D:/데이터/AI_PT_선택운동" --out out3d --csv2d out
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

from extract_pushup import CONDITIONS, JOINTS, FEATURES, angle, summarize, _nanmean


def _pt3(pts: dict, name: str) -> np.ndarray:
    p = pts.get(name)
    if not p or any(p.get(k) is None for k in "xyz"):
        return np.full(3, np.nan)
    return np.array([float(p["x"]), float(p["y"]), float(p["z"])])


def frame_features3(pts: dict) -> dict[str, float]:
    p = {f"{s}{j}": _pt3(pts, f"{side} {j}") for side, s in (("Left", "L"), ("Right", "R")) for j in JOINTS}
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


def load_flags(csv_dir: Path) -> dict[str, dict]:
    flags: dict[str, dict] = {}
    for f in sorted(csv_dir.glob("*.csv")):
        for r in csv.DictReader(f.open(encoding="utf-8-sig")):
            flags[r["type"]] = {c: r[c] for c in CONDITIONS.values()} | {"desc": r["desc"]}
    return flags


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--csv2d", required=True, help="extract_pushup.py가 만든 CSV 폴더 (유형별 정오 정보용)")
    a = ap.parse_args()
    root, out = Path(a.root), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    flags = load_flags(Path(a.csv2d))

    for split in ("Training", "Validation"):
        base = root / split / "푸시업" / "labels"
        if not base.is_dir():
            continue
        for day_dir in sorted(d for d in base.iterdir() if d.is_dir()):
            rows = []
            for p in sorted(day_dir.glob("*-3d.json")):
                m = re.search(r"-(\d{3})-3d$", p.stem)
                if not m or m.group(1) not in flags:
                    print(f"  건너뜀(유형 모름) {p.name}", file=sys.stderr)
                    continue
                t = m.group(1)
                j = json.loads(p.read_text(encoding="utf-8"))
                per = [frame_features3(fr["pts"]) for fr in j["frames"]]
                row = {"split": split, "day": day_dir.name, "clip": p.stem, "type": t,
                       "desc": flags[t]["desc"], "n_frames": len(per)}
                row |= {c: flags[t][c] for c in CONDITIONS.values()}
                row |= {f"D3_{k}": (round(v, 3) if not math.isnan(v) else "") for k, v in summarize(per).items()}
                rows.append(row)
            if rows:
                with (out / f"{split}_{day_dir.name}.csv").open("w", newline="", encoding="utf-8-sig") as fh:
                    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                    w.writeheader()
                    w.writerows(rows)
            print(f"완료 {split}/{day_dir.name}: {len(rows)}개 구간")


if __name__ == "__main__":
    main()
