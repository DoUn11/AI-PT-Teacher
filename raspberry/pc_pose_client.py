"""Pi 스트림을 받아 PC에서 YOLO Pose를 돌리고 구간별 지연시간을 측정한다."""
import argparse
import socket
import struct
import threading
import time

import av
import cv2
import numpy as np
from ultralytics import YOLO

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="192.168.1.101")
parser.add_argument("--port", type=int, default=8890)
parser.add_argument("--model", default="yolo11n-pose.pt")
parser.add_argument("--seconds", type=float, default=0, help="0이면 q 누를 때까지 실행")
parser.add_argument("--show", action="store_true", help="실시간 창 표시")
parser.add_argument("--save", default="", help="마지막 결과 프레임 저장 경로")
args = parser.parse_args()

HDR = struct.Struct("!ddI")
SYNC = struct.Struct("!d")
N_SYNC = 30


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("stream closed")
        buf += chunk
    return buf


sock = socket.create_connection((args.host, args.port), timeout=10)
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

# NTP 방식: RTT가 가장 짧은 샘플로 Pi-PC 시계 차이(offset = pi - pc) 추정
sock.sendall(SYNC.pack(N_SYNC))
samples = []
for _ in range(N_SYNC):
    t0 = time.time()
    sock.sendall(SYNC.pack(t0))
    pi_t = SYNC.unpack(recv_exact(sock, SYNC.size))[0]
    t2 = time.time()
    samples.append((t2 - t0, pi_t - (t0 + t2) / 2))
sync_rtt, offset = min(samples)
sock.settimeout(None)
print(f"clock sync: offset={offset * 1000:.2f}ms (rtt {sync_rtt * 1000:.2f}ms)")

lock = threading.Lock()
latest = None
recv_stats = {"frames": 0, "bytes": 0, "net": [], "pi": [], "dec": []}
stop = threading.Event()


def receiver():
    global latest
    codec = av.CodecContext.create("h264", "r")
    codec.thread_count = 1  # frame threading은 프레임 단위 지연을 추가하므로 끔
    seq = 0
    try:
        while not stop.is_set():
            cap_wall, send_wall, n = HDR.unpack(recv_exact(sock, HDR.size))
            data = recv_exact(sock, n)
            t_recv = time.time()
            recv_stats["bytes"] += n
            for frame in codec.decode(av.Packet(data)):
                img = frame.to_ndarray(format="bgr24")
                t_dec = time.time()
                seq += 1
                recv_stats["frames"] += 1
                recv_stats["pi"].append(send_wall - cap_wall)
                recv_stats["net"].append(t_recv - (send_wall - offset))
                recv_stats["dec"].append(t_dec - t_recv)
                with lock:
                    latest = (seq, img, cap_wall - offset, t_dec)
    except (ConnectionError, OSError):
        stop.set()


threading.Thread(target=receiver, daemon=True).start()

model = YOLO(args.model)
model(np.zeros((480, 640, 3), np.uint8), verbose=False)  # warm-up

inf_stats = {"wait": [], "inf": [], "total": [], "detected": 0, "frames": 0}
last_seq, annotated = 0, None
start = time.time()
while not stop.is_set():
    if args.seconds and time.time() - start > args.seconds:
        break
    with lock:
        item = latest
    if item is None or item[0] == last_seq:
        time.sleep(0.001)
        continue
    seq, img, cap_pc, t_dec = item
    last_seq = seq
    t_in = time.time()
    result = model(img, verbose=False)[0]
    t_done = time.time()

    inf_stats["frames"] += 1
    inf_stats["wait"].append(t_in - t_dec)
    inf_stats["inf"].append(t_done - t_in)
    inf_stats["total"].append(t_done - cap_pc)
    if len(result.boxes):
        inf_stats["detected"] += 1

    if args.show or args.save:
        annotated = result.plot()
        fps = inf_stats["frames"] / (time.time() - start)
        lat = (t_done - cap_pc) * 1000
        cv2.putText(annotated, f"{fps:.1f} fps  {lat:.0f} ms", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
    if args.show:
        cv2.imshow("pose", annotated)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

elapsed = time.time() - start
stop.set()
sock.close()
cv2.destroyAllWindows()
if args.save and annotated is not None:
    cv2.imwrite(args.save, annotated)


def ms(values):
    a = np.array(values) * 1000
    return f"mean {a.mean():6.1f} | p50 {np.percentile(a, 50):6.1f} | p95 {np.percentile(a, 95):6.1f} | max {a.max():6.1f}"


print(f"elapsed {elapsed:.1f}s | recv {recv_stats['frames'] / elapsed:.1f} fps"
      f" | infer {inf_stats['frames'] / elapsed:.1f} fps"
      f" | bitrate {recv_stats['bytes'] * 8 / elapsed / 1e6:.2f} Mbps"
      f" | detected {inf_stats['detected']}/{inf_stats['frames']}")
print(f"pi capture->send   {ms(recv_stats['pi'])}")
print(f"network pi->pc     {ms(recv_stats['net'])}")
print(f"decode             {ms(recv_stats['dec'])}")
print(f"queue wait         {ms(inf_stats['wait'])}")
print(f"inference          {ms(inf_stats['inf'])}")
print(f"TOTAL capture->pose {ms(inf_stats['total'])}")
