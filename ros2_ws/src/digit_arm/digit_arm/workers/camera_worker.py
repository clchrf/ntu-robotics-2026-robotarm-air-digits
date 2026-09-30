"""筆電鏡頭 + MediaPipe 手部追蹤工作程式。

在 Windows 上由 hand_gesture_node 透過 WSL interop 啟動（WSL2 沒有 /dev/video*）：
    python.exe camera_worker.py --model hand_landmarker.task --camera 0
也可在有鏡頭的 Linux 中由節點以 camera_backend:=local 直接在程序內呼叫 run()。

輸出：frame_protocol 封包（stdout 二進位）。畫面在這裡先水平鏡像，
所以關鍵點座標與使用者看到的畫面一致。標準輸入收到 quit 或關閉時釋放鏡頭並結束。
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from digit_arm.core import frame_protocol  # noqa: E402


def _open_camera(cv2, index: int, width: int, height: int, fps: int):
    backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY] if os.name == 'nt' else [cv2.CAP_V4L2, cv2.CAP_ANY]
    for api in backends:
        cap = cv2.VideoCapture(index, api)
        if cap is not None and cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            cap.set(cv2.CAP_PROP_FPS, fps)
            ok, _ = cap.read()
            if ok:
                return cap, api
            cap.release()
    return None, None


def _log(msg: str):
    sys.stderr.write(msg + '\n')
    sys.stderr.flush()


def run(args, emit, should_stop, on_ready=None) -> int:
    t0 = time.monotonic()
    try:
        import cv2
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
    except Exception as exc:  # noqa: BLE001
        emit({'type': 'error', 'code': 'missing_dependency',
              'message': f'OpenCV or MediaPipe is missing: {exc}'}, b'')
        return 3

    try:
        with open(args.model, 'rb') as fh:
            model_bytes = fh.read()
        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_buffer=model_bytes),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=args.min_detection,
            min_hand_presence_confidence=args.min_presence,
            min_tracking_confidence=args.min_tracking,
        )
        landmarker = vision.HandLandmarker.create_from_options(options)
        _log(f'Hand model loaded ({time.monotonic() - t0:.1f} s)')
    except Exception as exc:  # noqa: BLE001
        emit({'type': 'error', 'code': 'model_failed', 'message': f'Cannot load the hand model: {exc}'}, b'')
        return 4

    t1 = time.monotonic()
    cap, api = _open_camera(cv2, args.camera, args.width, args.height, args.fps)
    _log(f'Camera {"opened" if cap is not None else "failed"} ({time.monotonic() - t1:.1f} s, api={api})')
    if cap is None:
        emit({'type': 'error', 'code': 'camera_open_failed',
              'message': f'Cannot open camera {args.camera}. Another app may be using it, or access is blocked.'}, b'')
        landmarker.close()
        return 5

    emit({'type': 'status', 'state': 'opened', 'backend': str(api),
          'message': 'Camera open'}, b'')
    if on_ready is not None:
        on_ready()
    seq = 0
    failures = 0
    last_ts = -1
    t_start = time.monotonic()
    try:
        while not should_stop():
            ok, frame = cap.read()
            if not ok or frame is None:
                failures += 1
                if failures >= 30:
                    emit({'type': 'error', 'code': 'camera_lost', 'message': 'Camera picture stopped'}, b'')
                    return 6
                time.sleep(0.02)
                continue
            failures = 0
            frame = cv2.flip(frame, 1)  # 水平鏡像：像照鏡子一樣書寫
            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            ts = int((time.monotonic() - t_start) * 1000)
            if ts <= last_ts:
                ts = last_ts + 1
            last_ts = ts
            result = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
            hands = []
            for i, lms in enumerate(result.hand_landmarks or []):
                handed = ''
                score = 0.0
                if result.handedness and i < len(result.handedness) and result.handedness[i]:
                    handed = result.handedness[i][0].category_name
                    score = float(result.handedness[i][0].score)
                hands.append({'landmarks': [[round(p.x, 5), round(p.y, 5), round(p.z, 5)] for p in lms],
                              'handedness': handed, 'score': round(score, 3)})
            ok_jpg, jpg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), args.jpeg_quality])
            seq += 1
            emit({'type': 'frame', 'seq': seq, 't': time.time(), 'w': w, 'h': h, 'hands': hands},
                 jpg.tobytes() if ok_jpg else b'')
    finally:
        cap.release()
        landmarker.close()
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='Laptop camera + MediaPipe hand tracking')
    p.add_argument('--model', required=True)
    p.add_argument('--camera', type=int, default=0)
    p.add_argument('--width', type=int, default=640)
    p.add_argument('--height', type=int, default=480)
    p.add_argument('--fps', type=int, default=30)
    p.add_argument('--jpeg-quality', type=int, default=75)
    p.add_argument('--min-detection', type=float, default=0.6)
    p.add_argument('--min-presence', type=float, default=0.5)
    p.add_argument('--min-tracking', type=float, default=0.5)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    out = sys.stdout.buffer
    stop = threading.Event()
    write_lock = threading.Lock()

    def watch_stdin():
        try:
            for line in sys.stdin:
                if line.strip().lower() == 'quit':
                    break
        except Exception:  # noqa: BLE001
            pass
        stop.set()

    def start_watch():
        # 初始化完成後才讀 stdin：Windows 上對匿名管線的同步讀取會卡住其他執行緒對 stdin 的查詢，
        # 導致 import／模型載入慢十幾秒
        threading.Thread(target=watch_stdin, daemon=True).start()

    def emit(meta, jpeg):
        try:
            with write_lock:
                out.write(frame_protocol.pack(meta, jpeg))
                out.flush()
        except (BrokenPipeError, OSError, ValueError):
            stop.set()

    return run(args, emit, stop.is_set, on_ready=start_watch)


if __name__ == '__main__':
    sys.exit(main())
