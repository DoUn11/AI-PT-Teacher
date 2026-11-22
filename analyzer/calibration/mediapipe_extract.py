"""라벨이 있는 클립의 실제 이미지(클립당 32장)에 MediaPipe PoseLandmarker를 돌려 33점 좌표를 저장한다.

웹(frontend/src/camera.js)과 같은 조건으로 돌린다: pose_landmarker_lite, VIDEO 모드, 사람 1명,
입력은 640px 폭으로 줄인 이미지 (웹캠 640x480 과 비슷한 해상도). 클립마다 새 landmarker를 만들어
이전 클립의 추적 상태가 넘어가지 않게 하고, 프레임 간격은 1/15초로 둔다 (원본 fps는 알 수 없음).

필요: pip install mediapipe opencv-python numpy
모델: https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task

    python mediapipe_extract.py --root "D:/데이터/AI_PT_선택운동" --model pose_landmarker_lite.task --out mp \\
        --exercise kneeup sidecrunch pushup --workers 10

뷰 기본값: 서 있는 운동은 C(정면), 푸시업은 A (HANDOFF.md 결정). --view 로 바꿀 수 있다.
결과: out/<exercise>_<view>/<split>_<day>.npz (날짜별, 이미 있으면 건너뛴다 -> 중단 후 이어 하기 가능)
    clips  (N,)          라벨 파일 이름 (예: D01-1-033)
    lm     (N,32,33,4)   x, y(픽셀, 원본 해상도 기준), z(MediaPipe 상대 깊이), visibility. 못 찾으면 NaN
    size   (N,2)         원본 이미지 폭, 높이
    files  (N,32)        이미지 파일 이름 (프레임 번호 확인용)
이미지 폴더 이름은 라벨 img_key와 달리 뷰와 상관없이 *_A 로 되어 있어서 접두어로 찾는다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

EX_DIRS = {"kneeup": "스탠딩 니업", "sidecrunch": "스탠딩 사이드 크런치", "pushup": "푸시업"}
DEFAULT_VIEW = {"kneeup": "C", "sidecrunch": "C", "pushup": "A"}
VIEW_KEYS = {"A": "view1", "B": "view2", "C": "view3", "D": "view4", "E": "view5"}
TARGET_W = 640
FRAME_MS = 67
N_FRAMES = 32

_landmarker_bytes: bytes | None = None


def find_clip_dir(images: Path, img_key: str) -> Path | None:
    """img_key 예: Day01_200921_F/1/C/033-1-1-02-Z2_C/033-1-1-02-Z2_C-0000001.jpg"""
    parts = img_key.split("/")
    base = images.joinpath(*parts[:3])
    exact = base / parts[3]
    if exact.is_dir():
        return exact
    stem = parts[3].rsplit("_", 1)[0]
    cands = sorted(base.glob(stem + "_*"))
    return cands[0] if len(cands) == 1 else None


def list_clips(root: Path, ex: str, view: str) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """(split, day) -> [(clip 이름, 이미지 폴더)]"""
    out: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for split in ("Training", "Validation"):
        base = root / split / EX_DIRS[ex]
        if not (base / "labels").is_dir():
            continue
        for day_dir in sorted(d for d in (base / "labels").iterdir() if d.is_dir()):
            items = []
            for p in sorted(day_dir.glob("*.json")):
                if p.name.endswith("-3d.json"):
                    continue
                j = json.loads(p.read_text(encoding="utf-8"))
                fr = next((f[VIEW_KEYS[view]] for f in j["frames"] if VIEW_KEYS[view] in f), None)
                d = find_clip_dir(base / "images", fr["img_key"]) if fr else None
                if d is None:
                    print(f"  이미지 폴더 없음 {p.name}", file=sys.stderr)
                    continue
                items.append((p.stem, str(d)))
            if items:
                out[(split, day_dir.name)] = items
    return out


def _init(model_path: str) -> None:
    global _landmarker_bytes
    _landmarker_bytes = Path(model_path).read_bytes()  # 한글 경로 문제를 피하려고 버퍼로 넘긴다


def _read(path: Path, cv2):
    buf = np.fromfile(str(path), np.uint8)
    head = cv2.imdecode(buf, cv2.IMREAD_REDUCED_COLOR_4)  # 4K는 960, FHD는 480 폭
    full_w = head.shape[1] * 4
    flag = cv2.IMREAD_REDUCED_COLOR_4 if full_w >= TARGET_W * 4 else (
        cv2.IMREAD_REDUCED_COLOR_2 if full_w >= TARGET_W * 2 else cv2.IMREAD_COLOR)
    img = head if flag == cv2.IMREAD_REDUCED_COLOR_4 else cv2.imdecode(buf, flag)
    h, w = img.shape[:2]
    img = cv2.resize(img, (TARGET_W, round(h * TARGET_W / w)), interpolation=cv2.INTER_AREA)
    # 원본 크기: 축소 배율로 역산
    scale = {cv2.IMREAD_REDUCED_COLOR_4: 4, cv2.IMREAD_REDUCED_COLOR_2: 2}.get(flag, 1)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB), (w * scale, h * scale)


def run_clip(args: tuple[str, str]):
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    clip, d = args
    files = sorted(Path(d).glob("*.jpg"))[:N_FRAMES]
    lm = np.full((N_FRAMES, 33, 4), np.nan, dtype=np.float32)
    size = (np.nan, np.nan)
    opts = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_buffer=_landmarker_bytes),
        running_mode=vision.RunningMode.VIDEO, num_poses=1)
    with vision.PoseLandmarker.create_from_options(opts) as det:
        for i, f in enumerate(files):
            rgb, size = _read(f, cv2)
            res = det.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), i * FRAME_MS)
            if res.pose_landmarks:
                lm[i] = [[p.x * size[0], p.y * size[1], p.z, p.visibility] for p in res.pose_landmarks[0]]
    names = [f.name for f in files] + [""] * (N_FRAMES - len(files))
    return clip, lm, size, names


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True)
    ap.add_argument("--model", required=True, help="pose_landmarker_lite.task 경로")
    ap.add_argument("--out", required=True)
    ap.add_argument("--exercise", nargs="+", default=list(EX_DIRS), choices=list(EX_DIRS))
    ap.add_argument("--view", choices=list(VIEW_KEYS), help="기본: 서 있는 운동 C, 푸시업 A")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="날짜마다 앞에서 N개 클립만 (시험용)")
    a = ap.parse_args()

    root = Path(a.root)
    with ProcessPoolExecutor(a.workers, initializer=_init, initargs=(a.model,)) as pool:
        for ex in a.exercise:
            view = a.view or DEFAULT_VIEW[ex]
            out_dir = Path(a.out) / f"{ex}_{view}"
            out_dir.mkdir(parents=True, exist_ok=True)
            for (split, day), items in list_clips(root, ex, view).items():
                dst = out_dir / f"{split}_{day}.npz"
                if dst.exists():
                    print(f"있음 {ex} {split}/{day}")
                    continue
                if a.limit:
                    items = items[:a.limit]
                t0 = time.time()
                res = list(pool.map(run_clip, items, chunksize=2))
                lm = np.stack([r[1] for r in res])
                miss = float(np.isnan(lm[..., 0, 0]).mean())
                tmp = dst.with_name(dst.stem + ".tmp.npz")
                np.savez_compressed(tmp, clips=np.array([r[0] for r in res]), lm=lm,
                                    size=np.array([r[2] for r in res], dtype=float),
                                    files=np.array([r[3] for r in res]))
                tmp.replace(dst)
                print(f"완료 {ex}_{view} {split}/{day}: {len(res)}개 클립, 미검출 프레임 {miss:.1%}, "
                      f"{time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
