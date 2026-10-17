"""PC カメラから 1 枚撮影して保存する(実機試験で AI エージェントがモータの動きを目視確認するため)。

使い方: uv run --no-project --with opencv-python python tools/camera_snapshot.py <out.jpg> [camera_index]
"""
import sys
import time

import cv2

out = sys.argv[1]
idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
if not cap.isOpened():
    print("camera open failed", idx)
    sys.exit(1)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
# 自動露出が落ち着くまで数フレーム捨てる
t0 = time.time()
frame = None
while time.time() - t0 < 1.5:
    ok, frame = cap.read()
cap.release()
if frame is None:
    print("no frame")
    sys.exit(1)
cv2.imwrite(out, frame)
print("saved", out, frame.shape)
