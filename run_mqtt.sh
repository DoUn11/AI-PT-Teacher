#!/usr/bin/env bash
# MQTT 브로커(mosquitto)와 analyzer(수신·분석)를 한 번에 켠다. Ctrl+C 또는 영상 창에서 q로 종료.
#
#   ./run_mqtt.sh [운동] [analyzer 추가 옵션...]      운동: pushup(기본) | kneeup | sidecrunch
#   ./run_mqtt.sh kneeup --no-upload       # 백엔드 없이 화면만
#   ./run_mqtt.sh pushup --record rec      # 학습용 데이터 기록
#
# 분석 화면(관절선만 그린 영상)과 분석 정보(횟수·자세 조건 판정 JSON)를 같은 브로커로 다시 내보낸다.
# 백엔드(MQTT_URL 설정)와 웹 "Pi 카메라" 탭이 이것을 받아 영상 아래에 보여 준다.
# 웹의 '운동' 탭에서 운동을 고르고 '운동 시작'을 눌러야 세션이 열리고 횟수를 센다 (그 전에는 준비 상태: 자세만 표시).
# '운동 종료'를 누르면 세션이 닫히고 웹이 리포트로 이동한다. 인자의 운동은 처음 선택된 운동이다.
#
# 백엔드(8000)에 업로드하려고 이메일/비밀번호를 물어본다. 백엔드 없이 쓰려면 --no-upload.
# AIPT_EMAIL/AIPT_PASSWORD 환경변수가 있으면 묻지 않는다. 터미널이 아닌 곳(입력 불가)에서는 자동으로 --no-upload.
# 환경변수: MQTT_PORT(1883) MQTT_TOPIC(ai_pt/cam/frame) MQTT_ANALYZED_TOPIC(ai_pt/cam/analyzed)
#           MQTT_ANALYSIS_TOPIC(ai_pt/cam/analysis) MQTT_CONTROL_TOPIC(ai_pt/cam/control) AIPT_EMAIL AIPT_PASSWORD
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${MQTT_PORT:-1883}"
TOPIC="${MQTT_TOPIC:-ai_pt/cam/frame}"
ANALYZED_TOPIC="${MQTT_ANALYZED_TOPIC:-ai_pt/cam/analyzed}"
ANALYSIS_TOPIC="${MQTT_ANALYSIS_TOPIC:-ai_pt/cam/analysis}"
CONTROL_TOPIC="${MQTT_CONTROL_TOPIC:-ai_pt/cam/control}"
EXERCISE=pushup
if [[ $# -gt 0 && "$1" != -* ]]; then  # 첫 인자가 옵션(--...)이면 운동 이름이 아니다
  EXERCISE="$1"
  shift
fi

MOSQUITTO="$(command -v mosquitto || echo /opt/homebrew/opt/mosquitto/sbin/mosquitto)"
PYTHON="$ROOT/analyzer/.venv/bin/python"

[[ -x "$MOSQUITTO" ]] || { echo "mosquitto가 없습니다: brew install mosquitto" >&2; exit 1; }
[[ -x "$PYTHON" ]] || { echo "analyzer 환경이 없습니다: cd analyzer && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev,yolo,mqtt]'" >&2; exit 1; }

BROKER_PID=""
cleanup() {
  [[ -n "$BROKER_PID" ]] && kill "$BROKER_PID" 2>/dev/null && echo "브로커 종료"
  return 0
}
trap cleanup EXIT INT TERM

# 이미 떠 있는 브로커는 건드리지 않고 재사용한다 (그때는 종료할 때도 그대로 둔다)
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "브로커가 이미 :$PORT 에서 실행 중 - 재사용합니다"
else
  "$MOSQUITTO" -c "$ROOT/raspberry/mosquitto.conf" >"${TMPDIR:-/tmp}/aipt-mosquitto.log" 2>&1 &
  BROKER_PID=$!
  for _ in $(seq 20); do
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && break
    kill -0 "$BROKER_PID" 2>/dev/null || { echo "브로커 시작 실패. 로그: ${TMPDIR:-/tmp}/aipt-mosquitto.log" >&2; BROKER_PID=""; exit 1; }
    sleep 0.25
  done
  echo "브로커 시작 :$PORT (pid $BROKER_PID)"
fi

if [[ ! -t 0 && -z "${AIPT_EMAIL:-}" && " $* " != *" --no-upload "* ]]; then
  echo "입력할 수 없는 환경이라 백엔드 업로드 없이 실행합니다"
  set -- --no-upload "$@"
fi

echo "수신 대기: mqtt://localhost:$PORT/$TOPIC  (Pi는 --host <이 PC의 주소>)"
cd "$ROOT/analyzer"
"$PYTHON" -m aipt_analyzer --exercise "$EXERCISE" --source "mqtt://localhost:$PORT/$TOPIC" \
  --publish "mqtt://localhost:$PORT/$ANALYZED_TOPIC" --publish-info "mqtt://localhost:$PORT/$ANALYSIS_TOPIC" \
  --control "mqtt://localhost:$PORT/$CONTROL_TOPIC" "$@"
