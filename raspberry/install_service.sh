#!/usr/bin/env bash
# Pi 전원을 켜면 카메라 송신(pi_mqtt_publisher.py)이 자동으로 시작되게 systemd 서비스로 등록한다.
#
# Pi에서 한 번만 실행:
#   sudo ./install_service.sh <브로커(PC) 주소 또는 이름> [사용자]
#   예) sudo ./install_service.sh dounui-MacBookAir.local
#
# PC 주소는 네트워크마다 바뀌므로 IP 대신 PC 이름(.local)을 권장한다.
# 나중에 바꾸려면: sudo nano /etc/aipt-camera.env && sudo systemctl restart aipt-camera
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "sudo로 실행하세요: sudo $0 <브로커 주소>" >&2; exit 1; }
HOST="${1:?사용법: sudo $0 <브로커(PC) 주소 또는 이름> [사용자]}"
USER_NAME="${2:-${SUDO_USER:-ai-pt}}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
[[ -n "$HOME_DIR" && -d "$HOME_DIR" ]] || { echo "사용자를 찾을 수 없습니다: $USER_NAME" >&2; exit 1; }
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$SRC/pi_mqtt_publisher.py" ]] || { echo "pi_mqtt_publisher.py가 이 스크립트와 같은 폴더에 있어야 합니다" >&2; exit 1; }

# picamera2는 Pi OS 기본 패키지(시스템 파이썬)로 쓴다. pip 전역 설치는 막혀 있으므로 paho-mqtt도 apt로 설치한다.
apt-get install -y python3-picamera2 python3-paho-mqtt

install -m 755 -o "$USER_NAME" -g "$USER_NAME" "$SRC/pi_mqtt_publisher.py" "$HOME_DIR/pi_mqtt_publisher.py"

# 설정은 환경 파일에 둔다 (서비스 파일을 건드리지 않고 주소만 바꿀 수 있게)
if [[ ! -f /etc/aipt-camera.env ]] || [[ "${FORCE_ENV:-}" == 1 ]]; then
  cat > /etc/aipt-camera.env <<EOF
# MQTT 브로커(PC) 주소. 바꾼 뒤: sudo systemctl restart aipt-camera
MQTT_HOST=$HOST
MQTT_PORT=1883
MQTT_TOPIC=ai_pt/cam/frame
# 인증이 필요하면 아래 주석을 풀고 값을 넣는다 (이 파일은 root만 읽도록 되어 있다)
#MQTT_USERNAME=
#MQTT_PASSWORD=
EOF
  chmod 600 /etc/aipt-camera.env
else
  echo "/etc/aipt-camera.env가 이미 있어 그대로 둡니다 (덮어쓰려면 FORCE_ENV=1)"
fi

cat > /etc/systemd/system/aipt-camera.service <<EOF
[Unit]
Description=AI PT camera -> MQTT publisher
# 네트워크가 올라온 뒤에 시작한다
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
# 카메라는 video 그룹 권한이 필요하다
SupplementaryGroups=video
EnvironmentFile=/etc/aipt-camera.env
ExecStart=/usr/bin/python3 -u $HOME_DIR/pi_mqtt_publisher.py --host \${MQTT_HOST} --port \${MQTT_PORT} --topic \${MQTT_TOPIC}
# 카메라를 다른 프로그램이 잡고 있거나 일시적으로 실패해도 5초 뒤 다시 시작한다
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable aipt-camera
systemctl restart aipt-camera
sleep 3

echo
systemctl --no-pager --lines=8 status aipt-camera || true
cat <<EOF

설치 완료. 이제 Pi 전원을 켜면 자동으로 시작됩니다.
  상태 확인:   systemctl status aipt-camera
  실시간 로그: journalctl -u aipt-camera -f      (mqtt connected / sent=... 가 보이면 정상)
  주소 변경:   sudo nano /etc/aipt-camera.env && sudo systemctl restart aipt-camera
  끄기:        sudo systemctl disable --now aipt-camera
EOF
