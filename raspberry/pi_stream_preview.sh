#!/bin/bash
# Pi 화면에 카메라 미리보기를 띄우면서 TCP 8888로 송출. Ctrl+C로 종료.
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0}

pkill -f pi_rpicam_stream.sh
pkill rpicam-vid
sleep 1

trap 'pkill rpicam-vid; exit 0' INT TERM
while true; do
  rpicam-vid -t 0 --width 640 --height 480 --framerate 30 --inline --listen -o tcp://0.0.0.0:8888
  sleep 1
done
