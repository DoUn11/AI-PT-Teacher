"""mediapipe_extract.py 결과(npz)를 라벨 CSV와 같은 형식의 구간 요약 CSV로 바꾼다.

    python mediapipe_features.py --mp mp --labels-csv out_standing --pushup-csv out --out out_mp

라벨에서 뽑은 CSV의 행(정오 정보 ok_*, 유형, 라벨 좌표 특징 A_/C_/D3_)을 클립 이름으로 찾아 앞에 붙인다
(서 있는 운동: extract_standing.py 결과, 푸시업: extract_pushup.py 결과). 같은 구간끼리 라벨과 MediaPipe를 비교할 수 있다.
열 접두어:
    M_    클립의 32장 전부 사용 (시간 해상도 2배)
    M16_  라벨과 같은 16장(1, 3, ..., 31번째)만 사용 -> 라벨 대비 모델 차이만 보려고
visibility가 --min-conf(기본 0.3, 웹 설정 min_keypoint_conf와 같음)보다 낮은 점은 빈 값으로 둔다.
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import numpy as np

import extract_pushup as pu
import extract_standing as st

MP_INDEX = {
    "Nose": 0, "Left Ear": 7, "Right Ear": 8, "Left Shoulder": 11, "Right Shoulder": 12,
    "Left Elbow": 13, "Right Elbow": 14, "Left Wrist": 15, "Right Wrist": 16,
    "Left Hip": 23, "Right Hip": 24, "Left Knee": 25, "Right Knee": 26, "Left Ankle": 27, "Right Ankle": 28,
}


def to_pts(frame: np.ndarray, min_conf: float) -> dict:
    """(33,4) -> 라벨과 같은 {"Left Shoulder": {"x":..,"y":..}} 형식"""
    out = {}
    for name, i in MP_INDEX.items():
        x, y, _, vis = frame[i]
        out[name] = {"x": float(x), "y": float(y)} if np.isfinite(x) and vis >= min_conf else None
    return out


def summarize_pushup(pts_list: list[dict], prefix: str) -> dict:
    per = [pu.frame_features(p) for p in pts_list]
    return {f"{prefix}_{k}": (round(v, 4) if not math.isnan(v) else "") for k, v in pu.summarize(per).items()}


def summarize_standing(pts_list: list[dict], prefix: str) -> dict:
    per = [st.frame_features(st.load_pts(p, 2), st.UP_2D) for p in pts_list]
    return st.summarize(per, prefix)


def load_meta(path: Path) -> dict[tuple[str, str], dict]:
    """라벨 CSV의 행 전체(정오 정보 + 라벨 좌표 특징)를 (day, clip)으로 찾게 한다."""
    meta = {}
    for f in ([path] if path.is_file() else sorted(path.glob("*.csv"))):
        for r in csv.DictReader(f.open(encoding="utf-8-sig")):
            meta[(r["day"], r["clip"])] = r
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mp", required=True, help="mediapipe_extract.py --out 폴더")
    ap.add_argument("--labels-csv", required=True, help="extract_standing.py --out 폴더")
    ap.add_argument("--pushup-csv", help="extract_pushup.py --out 폴더")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-conf", type=float, default=0.3)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    for sub in sorted(Path(a.mp).iterdir()):
        if not sub.is_dir():
            continue
        ex = sub.name.rsplit("_", 1)[0]
        if ex == "pushup":
            if not a.pushup_csv:
                continue
            meta = load_meta(Path(a.pushup_csv))
            summ = summarize_pushup
        else:
            meta = load_meta(Path(a.labels_csv) / f"{ex}.csv")
            summ = summarize_standing
        rows = []
        for f in sorted(sub.glob("*.npz")):
            if f.name.endswith(".tmp.npz"):
                continue
            day = f.stem.split("_", 1)[1]
            z = np.load(f)
            for clip, lm in zip(z["clips"], z["lm"]):
                m = meta.get((day, str(clip)))
                if m is None:
                    continue
                pts = [to_pts(fr, a.min_conf) for fr in lm]
                row = dict(m)
                row |= summ(pts, "M") | summ(pts[0::2], "M16")
                row["M_detected"] = int(np.isfinite(lm[:, 0, 0]).sum())
                rows.append(row)
        if rows:
            dst = out / f"{sub.name}.csv"
            with dst.open("w", newline="", encoding="utf-8-sig") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            print(f"완료 {sub.name}: {len(rows)}개 구간 -> {dst}")


if __name__ == "__main__":
    main()
