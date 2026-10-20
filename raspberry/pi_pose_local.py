import sys
import time

import cv2
import numpy as np
import onnxruntime as ort
from picamera2 import Picamera2

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 20
SIZE = 320
CONF = 0.4
EDGES = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
         (11, 13), (13, 15), (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4)]

sess = ort.InferenceSession("yolo11n-pose.onnx", providers=["CPUExecutionProvider"])
inp = sess.get_inputs()[0].name


def infer(bgr):
    h, w = bgr.shape[:2]
    r = SIZE / max(h, w)
    nh, nw = int(h * r), int(w * r)
    canvas = np.full((SIZE, SIZE, 3), 114, np.uint8)
    top, left = (SIZE - nh) // 2, (SIZE - nw) // 2
    canvas[top:top + nh, left:left + nw] = cv2.resize(bgr, (nw, nh))
    x = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    out = sess.run(None, {inp: x})[0][0].T  # (N, 56)
    best = out[np.argmax(out[:, 4])]
    if best[4] < CONF:
        return None, 0.0
    kpts = best[5:].reshape(17, 3).copy()
    kpts[:, 0] = (kpts[:, 0] - left) / r
    kpts[:, 1] = (kpts[:, 1] - top) / r
    return kpts, float(best[4])


cam = Picamera2()
cam.configure(cam.create_video_configuration(main={"size": (640, 480), "format": "RGB888"}))
cam.start()
time.sleep(1)

frames = detected = 0
best_conf, best_img, last = -1.0, None, None
start = time.time()
while time.time() - start < DURATION:
    last = cam.capture_array()
    kpts, conf = infer(last)
    frames += 1
    if kpts is None:
        continue
    detected += 1
    if conf > best_conf:
        img = last.copy()
        pts = [(int(x), int(y)) for x, y, _ in kpts]
        for a, b in EDGES:
            if kpts[a, 2] > 0.5 and kpts[b, 2] > 0.5:
                cv2.line(img, pts[a], pts[b], (0, 255, 0), 3)
        for p, k in zip(pts, kpts):
            if k[2] > 0.5:
                cv2.circle(img, p, 4, (0, 0, 255), -1)
        best_conf, best_img = conf, img

elapsed = time.time() - start
cam.stop()
print(f"frames={frames} fps={frames / elapsed:.1f} detected={detected} best_conf={best_conf:.2f}")
cv2.imwrite("pose_result.jpg", best_img if best_img is not None else last)
print("person detected" if best_img is not None else "no person detected")
