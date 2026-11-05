"""주 특징 값의 오르내림으로 반복(rep)을 세는 상태 머신.

waiting(아직 위 자세를 못 봄) -> up -> moving/down(내려가는 중·바닥) -> 다시 up이면 한 번 완료.
count_threshold까지 내려가지 않았거나 min_rep_seconds보다 짧으면 세지 않는다.
임계값은 모두 ExerciseConfig에서 온다.
"""
from __future__ import annotations

from typing import Mapping

from aipt_analyzer.config import ExerciseConfig
from aipt_analyzer.results import RepSummary


class _Stat:
    __slots__ = ("min", "max", "total", "n")

    def __init__(self) -> None:
        self.min = float("inf")
        self.max = float("-inf")
        self.total = 0.0
        self.n = 0

    def add(self, v: float) -> None:
        self.min = min(self.min, v)
        self.max = max(self.max, v)
        self.total += v
        self.n += 1


class RepCounter:
    def __init__(self, cfg: ExerciseConfig, start_count: int = 0) -> None:
        self._cfg = cfg
        self.reps = start_count
        self.phase = "waiting"
        self._state = "waiting"  # waiting | up | moving
        self._reached = False
        self._start_t = 0.0
        self._stats: dict[str, _Stat] = {}

    def update(self, values: Mapping[str, float], t: float) -> RepSummary | None:
        """프레임 하나를 반영한다. 반복이 한 번 끝났으면 그 요약을 돌려준다."""
        cfg = self._cfg
        v = values[cfg.primary_feature]
        summary: RepSummary | None = None

        if self._state == "waiting":
            if v >= cfg.up_threshold:
                self._state = "up"
        elif self._state == "up":
            if v < cfg.up_threshold:
                self._state = "moving"
                self._reached = False
                self._start_t = t
                self._stats = {}

        if self._state == "moving":
            if v >= cfg.up_threshold:
                summary = self._finish(t)
                self._state = "up"
            else:
                for name, value in values.items():
                    self._stats.setdefault(name, _Stat()).add(value)
                if v <= cfg.count_threshold:
                    self._reached = True

        if self._state == "moving":
            self.phase = "down" if v <= cfg.count_threshold else "moving"
        else:
            self.phase = self._state
        return summary

    def _finish(self, t: float) -> RepSummary | None:
        cfg = self._cfg
        primary = self._stats.get(cfg.primary_feature)
        if not self._reached or primary is None or t - self._start_t < cfg.min_rep_seconds:
            return None
        self.reps += 1
        return RepSummary(
            index=self.reps,
            start_t=self._start_t,
            end_t=t,
            primary_feature=cfg.primary_feature,
            primary_min=primary.min,
            stats={
                name: {"min": s.min, "max": s.max, "mean": s.total / s.n}
                for name, s in self._stats.items()
            },
        )
