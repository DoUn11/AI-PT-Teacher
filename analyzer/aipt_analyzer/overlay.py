"""영상 위에 스켈레톤·각도·rep 수·피드백을 그린다.

한글은 cv2.putText로 그릴 수 없어 PIL 폰트를 쓴다. 폰트를 못 찾으면
영문 대체 문구로 표시한다 (AIPT_FONT 환경변수로 폰트 경로를 직접 지정할 수 있다).
"""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import math

from aipt_analyzer.config import ExerciseConfig
from aipt_analyzer.pose import SKELETON_EDGES
from aipt_analyzer.results import FrameResult, RepResult
from aipt_analyzer.severity import check_parts, check_severity, code_parts

_FONT_CANDIDATES = (
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)

GREEN = (0, 220, 0)
ORANGE = (0, 140, 255)
RED = (60, 60, 255)
WHITE = (255, 255, 255)
YELLOW = (0, 230, 255)
MIN_DRAW_CONF = 0.3

# 관절을 잇는 선만 그린다 (얼굴 점은 제외). 부위별로 색을 따로 낸다.
PART_EDGES = {
    "arms": ((5, 7), (7, 9), (6, 8), (8, 10)),
    "torso": ((5, 6), (5, 11), (6, 12), (11, 12)),
    "legs": ((11, 13), (13, 15), (12, 14), (14, 16)),
}
_RISE_S, _FALL_S = 0.2, 0.7  # 나빠질 땐 빠르게, 회복할 땐 천천히 색이 변한다
_FLASH_S = 2.0  # 지적을 받은 반복이 끝난 직후 해당 부위를 잠깐 붉게
_LIVE_FLAG_MIN = 0.75  # 판정기가 지금 지적한 부위는 최소한 이 정도로 붉게


def severity_color(sev: float) -> tuple[int, int, int]:
    """0(올바름)=초록 -> 0.5=노랑 -> 1(잘못됨)=빨강 (BGR). 값에 따라 연속적으로 변한다."""
    sev = min(1.0, max(0.0, sev))
    stops = ((0.0, GREEN), (0.5, (0, 220, 230)), (1.0, (40, 40, 255)))
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if sev <= b:
            f = (sev - a) / (b - a)
            return tuple(int(round(x + (y - x) * f)) for x, y in zip(ca, cb))
    return stops[-1][1]


def find_font() -> str | None:
    env = os.environ.get("AIPT_FONT")
    for path in ([env] if env else []) + list(_FONT_CANDIDATES):
        if path and Path(path).is_file():
            return path
    return None


class _TextLayer:
    """한 프레임의 글자를 모아 두었다가 한 번에 그린다 (PIL 변환 1회)."""

    def __init__(self, font_path: str | None, size: int) -> None:
        self._font_path = font_path
        self._size = size
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._items: list[tuple[str, str, tuple[int, int], tuple[int, int, int], float]] = []

    def add(self, ko: str, en: str, xy: tuple[int, int], color, scale: float = 1.0) -> None:
        self._items.append((ko, en, xy, color, scale))

    def _font(self, scale: float) -> ImageFont.FreeTypeFont:
        px = max(10, int(self._size * scale))
        if px not in self._fonts:
            self._fonts[px] = ImageFont.truetype(self._font_path, px)
        return self._fonts[px]

    def apply(self, frame: np.ndarray) -> np.ndarray:
        if not self._items:
            return frame
        if self._font_path is None:
            for _, en, (x, y), color, scale in self._items:
                cv2.putText(frame, en, (x, y + int(self._size * scale)), cv2.FONT_HERSHEY_SIMPLEX,
                            0.04 * self._size * scale, color, 2, cv2.LINE_AA)
            return frame
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img)
        for ko, _, xy, (b, g, r), scale in self._items:
            draw.text(xy, ko, font=self._font(scale), fill=(r, g, b))
        return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


class Overlay:
    def __init__(
        self,
        exercise: str,
        messages: dict[str, str],
        primary_feature: str,
        font_path: str | None = None,
        flash_seconds: float = 3.0,
        cfg: ExerciseConfig | None = None,
    ) -> None:
        self._cfg = cfg  # 있으면 잘못된 부위를 판정 기준에 맞춰 점점 붉게 칠한다
        self._sev = {part: 0.0 for part in PART_EDGES}
        self._flash_until = {part: 0.0 for part in PART_EDGES}
        self._last_now: float | None = None
        self._exercise = exercise
        self._messages = messages
        self._primary = primary_feature
        self._font_path = font_path if font_path is not None else find_font()
        self._flash_seconds = flash_seconds
        self._flash: tuple[RepResult, float] | None = None

    def _msg(self, code: str) -> tuple[str, str]:
        return self._messages.get(code, code), code  # 문구가 없으면 코드를 그대로 보여준다

    def render(self, frame: np.ndarray, result: FrameResult, now: float, text: bool = True) -> np.ndarray:
        """text=False면 글자·HUD 없이 관절선만 그린다 (정보는 백엔드로 따로 보내 웹에서 보여 줄 때)."""
        out = frame.copy()
        self._update_severity(result, now)
        if not text:
            self._draw_skeleton(out, result)
            return out
        h, w = out.shape[:2]
        text = _TextLayer(self._font_path, max(14, h // 24))
        line = text._size + 6

        if result.rep is not None:
            self._flash = (result.rep, now + self._flash_seconds)

        self._draw_skeleton(out, result)
        self._draw_hud(out, text, result, line)

        if not result.visible:
            ko, en = self._msg("no_person")
            text.add(ko, en, (12, h // 2), YELLOW)

        # 진행 중 피드백 (아래쪽)
        for i, code in enumerate(result.live_feedback):
            ko, en = self._msg(code)
            text.add(ko, en, (12, h - line * (len(result.live_feedback) - i) - 8), RED, 1.2)

        # 방금 끝난 반복 결과 (오른쪽 위)
        if self._flash is not None:
            rep, until = self._flash
            if now <= until:
                color = GREEN if rep.score >= 80 else ORANGE
                x = int(w * 0.52)
                text.add(f"{rep.summary.index}회 완료  {rep.score}점",
                         f"Rep {rep.summary.index}  {rep.score}", (x, 8), color, 1.2)
                for i, code in enumerate(rep.feedback):
                    ko, en = self._msg(code)
                    text.add(ko, en, (x, 8 + line * (i + 2)), ORANGE)
            else:
                self._flash = None

        return text.apply(out)

    def _targets(self, result: FrameResult, now: float) -> dict[str, float]:
        """부위별로 '지금 얼마나 잘못됐는가'(0~1). 판정 설정이 없으면 지적이 있을 때 전체를 붉게.

        실시간(live) 조건은 지금 값으로 점점 붉게, 반복이 끝난 뒤에만 판정하는 조건(live=False)과
        깊이 부족은 반복이 끝난 직후 해당 부위를 잠깐 붉게 한다. 꺼 둔(enabled=False) 조건은 무시한다.
        """
        target = {part: 0.0 for part in PART_EDGES}
        cfg = self._cfg
        if cfg is None:
            if result.live_feedback:
                target = {part: 1.0 for part in PART_EDGES}
            return target

        def raise_to(parts, value):
            for part in parts:
                target[part] = max(target[part], value)

        if result.visible:
            flagged = set(result.live_feedback)
            for check in cfg.form_checks:
                if not (check.enabled and check.live):
                    continue
                value = result.features.get(check.feature)
                if value is not None:
                    raise_to(check_parts(check), check_severity(check, value))
                if check.code in flagged:
                    raise_to(check_parts(check), _LIVE_FLAG_MIN)

        if result.rep is not None:
            for code in result.rep.feedback:
                for part in code_parts(code, cfg):
                    self._flash_until[part] = now + _FLASH_S
        for part, until in self._flash_until.items():
            if now <= until:
                target[part] = 1.0
        return target

    def _update_severity(self, result: FrameResult, now: float) -> None:
        dt = 0.0 if self._last_now is None else max(0.0, now - self._last_now)
        self._last_now = now
        for part, goal in self._targets(result, now).items():
            cur = self._sev[part]
            tau = _RISE_S if goal > cur else _FALL_S
            self._sev[part] = cur + (goal - cur) * (1 - math.exp(-dt / tau))

    def _draw_skeleton(self, out: np.ndarray, result: FrameResult) -> None:
        pose = result.pose
        if pose is None:
            return
        node_sev: dict[int, float] = {}
        for part, edges in PART_EDGES.items():
            sev = self._sev[part]
            color = severity_color(sev)
            for a, b in edges:
                if pose.conf[a] >= MIN_DRAW_CONF and pose.conf[b] >= MIN_DRAW_CONF:
                    pa = tuple(int(v) for v in pose.xy[a])
                    pb = tuple(int(v) for v in pose.xy[b])
                    cv2.line(out, pa, pb, color, 4, cv2.LINE_AA)
                    for n in (a, b):
                        node_sev[n] = max(node_sev.get(n, 0.0), sev)
        for n, sev in node_sev.items():
            cv2.circle(out, tuple(int(v) for v in pose.xy[n]), 5, severity_color(sev), -1, cv2.LINE_AA)

    def _draw_hud(self, out: np.ndarray, text: _TextLayer, result: FrameResult, line: int) -> None:
        panel_w, panel_h = int(text._size * 11), line * 3 + 12
        roi = out[0:panel_h, 0:panel_w]
        cv2.addWeighted(np.zeros_like(roi), 0.55, roi, 0.45, 0, roi)

        text.add(f"{self._exercise}  {result.reps}회", f"{self._exercise}  {result.reps} reps",
                 (10, 6), WHITE, 1.3)
        value = result.features.get(self._primary)
        angle = f"{self._primary} {value:.0f}°" if value is not None else f"{self._primary} --"
        text.add(angle, angle.replace("°", " deg"), (10, 6 + line + 4), YELLOW)
        ko, en = self._msg(f"phase.{result.phase}")
        text.add(ko, en, (10, 6 + 2 * line + 4), GREEN)
