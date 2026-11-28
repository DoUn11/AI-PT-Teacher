"""Pi 카메라 프레임을 JPEG로 인코딩해 MQTT 토픽으로 발행한다.

수신: analyzer에서 `--source mqtt://<브로커>[:1883]/<토픽>`
인증이 필요하면 환경변수 MQTT_USERNAME / MQTT_PASSWORD (명령줄 인자는 프로세스 목록에 남는다).
"""
import argparse
import io
import os
import time

import paho.mqtt.client as mqtt
from picamera2 import Picamera2

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="localhost", help="MQTT 브로커 주소 (브로커를 Pi에 두면 localhost)")
parser.add_argument("--port", type=int, default=1883)
parser.add_argument("--topic", default="ai_pt/cam/frame")  # 백엔드/analyzer 기본 구독 토픽과 같다
parser.add_argument("--tls", action="store_true", help="TLS로 접속 (보통 --port 8883)")
parser.add_argument("--width", type=int, default=640)
parser.add_argument("--height", type=int, default=480)
parser.add_argument("--fps", type=int, default=15, help="JPEG는 H.264보다 무겁다. 15fps 정도부터 시작")
parser.add_argument("--quality", type=int, default=70, help="JPEG 품질 1~100")
args = parser.parse_args()

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if os.environ.get("MQTT_USERNAME"):
    client.username_pw_set(os.environ["MQTT_USERNAME"], os.environ.get("MQTT_PASSWORD"))
if args.tls:
    client.tls_set()
client.reconnect_delay_set(min_delay=1, max_delay=10)
client.on_connect = lambda c, u, f, rc, p=None: print(f"mqtt connected: {rc}", flush=True)
client.on_disconnect = lambda c, u, f, rc, p=None: print(f"mqtt disconnected: {rc}", flush=True)
client.connect_async(args.host, args.port)  # 브로커가 늦게 떠도 계속 재시도한다
client.loop_start()

cam = Picamera2()
cam.options["quality"] = args.quality
cam.configure(cam.create_video_configuration(
    main={"size": (args.width, args.height)},
    controls={"FrameDurationLimits": (1_000_000 // args.fps, 1_000_000 // args.fps)}))
cam.start()
print(f"publishing {args.width}x{args.height}@{args.fps} q{args.quality} -> "
      f"{args.host}:{args.port} {args.topic}", flush=True)

interval = 1 / args.fps
pending = None  # 직전에 발행한 메시지. 아직 소켓에 못 나갔으면 이번 프레임은 버린다 (지연이 쌓이지 않게)
sent = dropped = 0
last_report = time.monotonic()
next_t = time.monotonic()
try:
    while True:
        buf = io.BytesIO()
        cam.capture_file(buf, format="jpeg")  # 다음 프레임이 올 때까지 블록되므로 fps가 자연히 맞춰진다
        if pending is not None and not pending.is_published():
            dropped += 1
        else:
            pending = client.publish(args.topic, buf.getvalue(), qos=0)
            if pending.rc == mqtt.MQTT_ERR_SUCCESS:
                sent += 1
            else:  # 브로커 연결이 끊긴 동안
                pending = None
                dropped += 1

        next_t += interval
        if (delay := next_t - time.monotonic()) > 0:
            time.sleep(delay)
        else:
            next_t = time.monotonic()

        if time.monotonic() - last_report >= 10:
            print(f"sent={sent} dropped={dropped}", flush=True)
            last_report = time.monotonic()
except KeyboardInterrupt:
    pass
finally:
    cam.stop()
    client.disconnect()
    client.loop_stop()
