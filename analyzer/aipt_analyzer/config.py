"""운동 설정(JSON) 로더.

운동 하나 = JSON 파일 하나. 코드를 고치지 않고 파일만 바꿔서
새 운동을 추가하거나 학습된 임계값으로 교체할 수 있다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aipt_analyzer.features import FEATURES

CONFIG_DIR = Path(__file__).parent / "configs"


@dataclass(frozen=True)
class DepthRule:
    """주 특징이 target까지 내려갔는지 본다. 깊이가 부족할수록 점수를 깎는다."""

    code: str
    target: float
    tolerance: float  # target + tolerance 보다 얕으면 피드백 코드를 낸다
    penalty_per_degree: float
    min_score: float  # 깊이 때문에 떨어질 수 있는 점수의 하한


@dataclass(frozen=True)
class FormCheck:
    """반복 동안의 특징 통계가 기준을 벗어나면 감점한다.

    stat="max"면 최댓값이 limit 이하여야 하고, stat="min"이면 최솟값이 limit 이상이어야 한다.
    """

    code: str
    feature: str
    stat: str  # "min" | "max"
    limit: float
    penalty: float


@dataclass(frozen=True)
class ExerciseConfig:
    exercise: str
    version: int
    calibrated: bool  # 실측/학습 데이터로 보정된 값인가
    primary_feature: str  # rep을 세는 기준 특징 (위 -> 아래 -> 위로 움직이는 값)
    up_threshold: float  # 이 값 이상이면 시작 자세(위)
    count_threshold: float  # 이 값 이하까지 내려가야 한 번으로 센다
    smoothing: float  # 새 샘플의 가중치 (0, 1]. 1이면 스무딩 없음
    min_rep_seconds: float  # 이보다 짧은 반복은 떨림으로 보고 버린다
    min_keypoint_conf: float
    depth: DepthRule
    form_checks: tuple[FormCheck, ...] = ()
    judge: dict[str, Any] = field(default_factory=lambda: {"type": "threshold"})

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExerciseConfig":
        try:
            depth = DepthRule(**d["depth"])
            checks = tuple(FormCheck(**c) for c in d.get("form_checks", []))
            cfg = cls(
                exercise=d["exercise"],
                version=int(d["version"]),
                calibrated=bool(d.get("calibrated", False)),
                primary_feature=d["primary_feature"],
                up_threshold=float(d["up_threshold"]),
                count_threshold=float(d["count_threshold"]),
                smoothing=float(d.get("smoothing", 0.6)),
                min_rep_seconds=float(d.get("min_rep_seconds", 0.4)),
                min_keypoint_conf=float(d.get("min_keypoint_conf", 0.3)),
                depth=depth,
                form_checks=checks,
                judge=dict(d.get("judge", {"type": "threshold"})),
            )
        except (KeyError, TypeError) as e:
            raise ValueError(f"invalid exercise config: {e!r}") from e
        cfg._validate()
        return cfg

    def _validate(self) -> None:
        if self.primary_feature not in FEATURES:
            raise ValueError(f"unknown primary_feature {self.primary_feature!r}; known: {sorted(FEATURES)}")
        if not self.count_threshold < self.up_threshold:
            raise ValueError("count_threshold must be lower than up_threshold")
        if not 0 < self.smoothing <= 1:
            raise ValueError("smoothing must be in (0, 1]")
        if self.min_rep_seconds < 0:
            raise ValueError("min_rep_seconds must be >= 0")
        if not 0 <= self.depth.min_score <= 100:
            raise ValueError("depth.min_score must be in [0, 100]")
        for c in self.form_checks:
            if c.feature not in FEATURES:
                raise ValueError(f"form check {c.code!r}: unknown feature {c.feature!r}")
            if c.stat not in ("min", "max"):
                raise ValueError(f"form check {c.code!r}: stat must be 'min' or 'max'")


def available_exercises() -> list[str]:
    return sorted(p.stem for p in CONFIG_DIR.glob("*.json") if p.stem != "messages.ko")


def load_exercise_config(name_or_path: str, override_path: str | Path | None = None) -> ExerciseConfig:
    """운동 이름(내장 설정) 또는 JSON 파일 경로로 불러온다.

    override_path를 주면 이름은 운동 식별용으로만 쓰고 그 파일의 내용을 사용한다
    (학습된 임계값 파일로 교체할 때).
    """
    path = Path(override_path) if override_path else Path(name_or_path)
    if not path.suffix:
        path = CONFIG_DIR / f"{name_or_path}.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"exercise config not found: {path} (available built-in: {', '.join(available_exercises())})"
        )
    return ExerciseConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))


def load_messages(path: str | Path | None = None) -> dict[str, str]:
    """피드백 코드 -> 화면에 보여줄 문구."""
    p = Path(path) if path else CONFIG_DIR / "messages.ko.json"
    return json.loads(p.read_text(encoding="utf-8"))
