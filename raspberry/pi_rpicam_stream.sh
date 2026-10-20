#!/bin/bash
while true; do
  rpicam-vid -n -t 0 --width 640 --height 480 --framerate 30 --inline --listen -o tcp://0.0.0.0:8888
  sleep 1
done
