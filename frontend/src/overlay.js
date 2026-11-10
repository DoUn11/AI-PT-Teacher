// 영상 위에 스켈레톤·각도·rep 수·피드백을 그린다 (analyzer/overlay.py와 같은 구성).
const EDGES = [
  ["left_shoulder", "right_shoulder"], ["left_hip", "right_hip"],
  ["left_shoulder", "left_elbow"], ["left_elbow", "left_wrist"],
  ["right_shoulder", "right_elbow"], ["right_elbow", "right_wrist"],
  ["left_shoulder", "left_hip"], ["right_shoulder", "right_hip"],
  ["left_hip", "left_knee"], ["left_knee", "left_ankle"],
  ["right_hip", "right_knee"], ["right_knee", "right_ankle"],
];
const MIN_DRAW_CONF = 0.3;
const COLORS = { normal: "#ffffff", muted: "#dddddd", warning: "#ffffff", white: "#ffffff" };
const FONT = '"Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif';

export const FLASH_MS = 3000;

// messages: 피드백 코드 -> 문구. 없으면 코드를 그대로 보여준다.
export function drawOverlay(ctx, w, h, frame, pose, { exercise, primaryFeature, messages, flash, now }) {
  ctx.clearRect(0, 0, w, h);
  const size = Math.max(14, Math.round(h / 24));
  const line = size + 6;
  const msg = (code) => messages[code] ?? code;
  const text = (s, x, y, color, scale = 1) => {
    ctx.font = `${Math.round(size * scale)}px ${FONT}`;
    ctx.textBaseline = "top";
    ctx.fillStyle = color;
    ctx.fillText(s, x, y);
  };

  if (pose) drawSkeleton(ctx, pose, frame.liveFeedback.length > 0);

  // HUD
  ctx.fillStyle = "rgba(0,0,0,0.55)";
  ctx.fillRect(0, 0, size * 11, line * 3 + 12);
  text(`${exercise}  ${frame.reps}회`, 10, 6, COLORS.white, 1.3);
  const v = frame.features[primaryFeature];
  text(v === undefined ? `${primaryFeature} --` : `${primaryFeature} ${v.toFixed(0)}°`, 10, 6 + line + 4, COLORS.muted);
  text(msg(`phase.${frame.phase}`), 10, 6 + 2 * line + 4, COLORS.normal);

  if (!frame.visible) text(msg("no_person"), 12, h / 2, COLORS.muted);

  // 진행 중 피드백 (아래쪽)
  frame.liveFeedback.forEach((code, i) => {
    text(msg(code), 12, h - line * (frame.liveFeedback.length - i) - 8, COLORS.warning, 1.2);
  });

  // 방금 끝난 반복 결과 (오른쪽 위)
  if (flash && now <= flash.until) {
    const x = Math.round(w * 0.52);
    text(`${flash.rep.summary.index}회 완료  ${flash.rep.score}점`, x, 8,
      flash.rep.score >= 80 ? COLORS.normal : COLORS.muted, 1.2);
    flash.rep.feedback.forEach((code, i) => text(msg(code), x, 8 + line * (i + 2), COLORS.muted));
  }
}

function drawSkeleton(ctx, pose, warn) {
  ctx.lineWidth = 3;
  ctx.setLineDash(warn ? [8, 5] : []);
  ctx.strokeStyle = warn ? COLORS.warning : COLORS.normal;
  for (const [a, b] of EDGES) {
    if (pose.conf[a] >= MIN_DRAW_CONF && pose.conf[b] >= MIN_DRAW_CONF) {
      ctx.beginPath();
      ctx.moveTo(...pose.xy[a]);
      ctx.lineTo(...pose.xy[b]);
      ctx.stroke();
    }
  }
  ctx.setLineDash([]);
  ctx.fillStyle = COLORS.white;
  for (const name of Object.keys(pose.xy)) {
    if (pose.conf[name] >= MIN_DRAW_CONF) {
      ctx.beginPath();
      ctx.arc(pose.xy[name][0], pose.xy[name][1], 5, 0, 2 * Math.PI);
      ctx.fill();
    }
  }
}
