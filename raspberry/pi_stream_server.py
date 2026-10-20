"""Pi 카메라 H.264 스트리밍 서버 (프레임마다 촬영 시각을 붙여 전송)."""
import argparse
import socket
import struct
import time

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import Output

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8890)
parser.add_argument("--width", type=int, default=640)
parser.add_argument("--height", type=int, default=480)
parser.add_argument("--fps", type=int, default=30)
parser.add_argument("--bitrate", type=int, default=4_000_000)
args = parser.parse_args()

HDR = struct.Struct("!ddI")  # capture_wall, send_wall, payload_len
SYNC = struct.Struct("!d")

cam = Picamera2()
cam.configure(cam.create_video_configuration(
    main={"size": (args.width, args.height)},
    controls={"FrameDurationLimits": (1_000_000 // args.fps, 1_000_000 // args.fps)}))

state = {"first": None, "sensor": set()}


def pre_callback(request):
    ts = request.get_metadata()["SensorTimestamp"]
    if state["first"] is None:
        state["first"] = ts
    state["sensor"].add(ts)


cam.pre_callback = pre_callback


class SocketOutput(Output):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.alive = True
        self.frames = self.ts_mismatch = 0
        self.wall_minus_mono = time.time() - time.clock_gettime(time.CLOCK_MONOTONIC)

    def outputframe(self, frame, keyframe=True, timestamp=None, packet=None, audio=False):
        # encoder timestamps are microseconds relative to the first frame's SensorTimestamp
        sensor_ns = state["first"] + timestamp * 1000
        if not any(abs(sensor_ns - s) < 1_000_000 for s in state["sensor"]):
            self.ts_mismatch += 1
        state["sensor"] = {s for s in state["sensor"] if s > sensor_ns}
        capture_wall = sensor_ns / 1e9 + self.wall_minus_mono
        try:
            self.conn.sendall(HDR.pack(capture_wall, time.time(), len(frame)) + bytes(frame))
            self.frames += 1
        except OSError:
            self.alive = False


def recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError
        buf += chunk
    return buf


srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("0.0.0.0", args.port))
srv.listen(1)
print(f"listening :{args.port} {args.width}x{args.height}@{args.fps} {args.bitrate // 1000}kbps", flush=True)

while True:
    conn, addr = srv.accept()
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        n_sync = SYNC.unpack(recv_exact(conn, SYNC.size))[0]
        for _ in range(int(n_sync)):
            recv_exact(conn, SYNC.size)
            conn.sendall(SYNC.pack(time.time()))
    except (ConnectionError, OSError):
        conn.close()
        continue

    print(f"client {addr[0]} connected", flush=True)
    state["first"], state["sensor"] = None, set()
    out = SocketOutput(conn)
    cam.start_recording(H264Encoder(bitrate=args.bitrate, repeat=True, iperiod=args.fps), out)
    while out.alive:
        time.sleep(0.2)
    cam.stop_recording()
    conn.close()
    print(f"client gone: sent={out.frames} ts_mismatch={out.ts_mismatch}", flush=True)
