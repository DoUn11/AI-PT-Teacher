"""프레임 파이프라인: Pose -> 특징 -> 스무딩 -> rep 카운터 -> 판정."""
from __future__ import annotations

from aipt_analyzer.config import ExerciseConfig
from aipt_analyzer.counter import RepCounter
from aipt_analyzer.features import extract_features
from aipt_analyzer.judges import RepJudge, build_judge
from aipt_analyzer.pose import Pose
from aipt_analyzer.results import FrameResult, RepResult

# 이 시간 넘게 몸이 안 보이면 스무딩 이력을 버린다 (오래된 값에 끌리지 않게)
_SMOOTHING_RESET_GAP_S = 1.0


class Analyzer:
    def __init__(
        self,
        cfg: ExerciseConfig,
        judge: RepJudge | None = None,
        start_count: int = 0,
    ) -> None:
        self.cfg = cfg
        self.judge = judge or build_judge(cfg)
        self.counter = RepCounter(cfg, start_count=start_count)
        self._smoothed: dict[str, float] = {}
        self._last_t: float | None = None

    def process(self, pose: Pose | None, t: float) -> FrameResult:
        values = extract_features(pose, self.cfg.min_keypoint_conf).values if pose else {}

        if self.cfg.primary_feature not in values:
            return FrameResult(
                visible=False,
                pose=pose,
                features=dict(self._smoothed),
                phase=self.counter.phase,
                reps=self.counter.reps,
            )

        smoothed = self._smooth(values, t)
        summary = self.counter.update(smoothed, t)
        phase = self.counter.phase
        live = self.judge.live_feedback(smoothed) if phase in ("moving", "down") else []

        rep = None
        if summary is not None:
            j = self.judge.judge(summary)
            rep = RepResult(summary=summary, score=j.score, feedback=j.feedback)

        return FrameResult(
            visible=True,
            pose=pose,
            features=smoothed,
            phase=phase,
            reps=self.counter.reps,
            live_feedback=live,
            rep=rep,
        )

    def _smooth(self, values: dict[str, float], t: float) -> dict[str, float]:
        if self._last_t is not None and t - self._last_t > _SMOOTHING_RESET_GAP_S:
            self._smoothed = {}
        self._last_t = t
        a = self.cfg.smoothing
        out = {}
        for name, v in values.items():
            prev = self._smoothed.get(name)
            out[name] = v if prev is None else a * v + (1 - a) * prev
        self._smoothed.update(out)
        return out
