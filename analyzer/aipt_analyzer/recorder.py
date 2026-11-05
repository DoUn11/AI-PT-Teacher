"""학습용 데이터 기록.

세션마다 폴더 하나를 만들고 다음을 저장한다.
  meta.json    운동, 시작 시각, 사용한 설정 전체(버전 포함)
  frames.jsonl 프레임마다 {t, visible, phase, keypoints, features}
  reps.jsonl   끝난 반복마다 요약 통계 + 점수/피드백

keypoints는 모델 출력 원본이라, 특징 정의를 바꿔도 frames.jsonl에서 다시 계산할 수 있다.
rep 판정에 사람이 붙일 라벨은 reps.jsonl의 index를 키로 별도 파일에 만들어 쓰면 된다.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

from aipt_analyzer.config import ExerciseConfig
from aipt_analyzer.results import FrameResult, RepResult


class SessionRecorder:
    def __init__(self, root: str | Path, cfg: ExerciseConfig) -> None:
        now = datetime.now(timezone.utc)
        self.dir = Path(root) / f"{cfg.exercise}-{now.strftime('%Y%m%dT%H%M%SZ')}"
        self.dir.mkdir(parents=True, exist_ok=False)
        meta = {
            "exercise": cfg.exercise,
            "started_at": now.isoformat(),
            "config": dataclasses.asdict(cfg),
        }
        (self.dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        self._frames = open(self.dir / "frames.jsonl", "w", encoding="utf-8", buffering=1)
        self._reps = open(self.dir / "reps.jsonl", "w", encoding="utf-8", buffering=1)

    def frame(self, t: float, result: FrameResult) -> None:
        row = {
            "t": round(t, 3),
            "visible": result.visible,
            "phase": result.phase,
            "keypoints": result.pose.to_rows() if result.pose else None,
            "features": {k: round(v, 2) for k, v in result.features.items()},
        }
        self._frames.write(json.dumps(row) + "\n")

    def rep(self, rep: RepResult) -> None:
        self._reps.write(json.dumps(rep.to_dict(), ensure_ascii=False) + "\n")

    def close(self) -> None:
        self._frames.close()
        self._reps.close()
