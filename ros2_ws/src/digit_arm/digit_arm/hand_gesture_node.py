"""鏡頭與手勢節點：取得鏡像影像、追蹤食指指尖、判斷下筆／抬筆／握拳、累積筆跡。

發布
  /digit_arm/camera/image/compressed  sensor_msgs/CompressedImage（水平鏡像，best effort）
  /digit_arm/camera/status            CameraStatus（latched）
  /digit_arm/hand_state               HandState（每格）
  /digit_arm/ink                      Ink（latched，書寫中約 20 Hz）
  /digit_arm/ink/submitted            Ink（握拳穩定維持後送出一次）
服務
  /digit_arm/camera/enable            std_srvs/SetBool（開／關鏡頭）
  /digit_arm/ink/clear                std_srvs/Trigger（清除筆跡、解除鎖定）

影像來源（camera_backend）
  windows   — WSL 中經 interop 執行 Windows python.exe 的 workers/camera_worker.py（預設）
  local     — 本機直接開鏡頭（原生 Linux 需要 mediapipe）
  synthetic — 合成手勢，寫出 synthetic_text（僅供測試，畫面會標示不是鏡頭）
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from typing import Callable, Optional

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage
from std_srvs.srv import SetBool, Trigger

from digit_arm_interfaces.msg import CameraStatus, HandState, Ink, Stroke

from .core import frame_protocol, gestures, winproc
from .core.pen import HandObservation, PenConfig, PenTracker

LATCHED = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST,
                     reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)
SENSOR = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.BEST_EFFORT)

PacketFn = Callable[[dict, bytes], None]


class WindowsWorkerSource:
    label = 'Windows laptop camera'

    def __init__(self, python_exe: str, script: str, args: list, on_packet: PacketFn, on_exit, log):
        self.cmd = [python_exe, '-u', script] + args
        self.on_packet = on_packet
        self.on_exit = on_exit
        self.log = log
        self.proc: Optional[subprocess.Popen] = None
        self._stderr = deque(maxlen=20)
        self.stopping = False

    def start(self):
        self.stopping = False
        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, bufsize=0)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read(self):
        proc = self.proc
        error = None
        try:
            while True:
                pkt = frame_protocol.read_packet(proc.stdout)
                if pkt is None:
                    break
                self.on_packet(*pkt)
        except Exception as exc:  # noqa: BLE001
            error = f'Could not read camera data: {exc}'
        if self.stopping and proc.poll() is None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        tail = ' / '.join(list(self._stderr)[-3:])
        self.on_exit(self, self.stopping, error or (f'Camera program stopped: {tail}' if tail else 'Camera program stopped unexpectedly'))

    def _read_err(self):
        for raw in self.proc.stderr:
            line = raw.decode('utf-8', errors='replace').rstrip()
            if line:
                self._stderr.append(line)
                self.log(f'[camera_worker] {line}')

    def stop(self):
        self.stopping = True
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.stdin.write(b'quit\n')
            proc.stdin.flush()
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()


class LocalSource:
    label = 'Local camera'

    def __init__(self, args: list, on_packet: PacketFn, on_exit, log):
        self.args = args
        self.on_packet = on_packet
        self.on_exit = on_exit
        self._stop = threading.Event()
        self.stopping = False

    def start(self):
        from .workers import camera_worker
        self._stop.clear()
        self.stopping = False
        parsed = camera_worker.parse_args(self.args)

        def body():
            camera_worker.run(parsed, self.on_packet, self._stop.is_set)
            self.on_exit(self, self.stopping, 'Camera stopped')
        threading.Thread(target=body, daemon=True).start()

    def stop(self):
        self.stopping = True
        self._stop.set()


class SyntheticSource:
    """合成手勢來源：不開鏡頭，播放「寫 synthetic_text → 握拳」的關鍵點序列。"""
    label = 'Synthetic test (not a camera)'

    def __init__(self, text: str, on_packet: PacketFn, on_exit, log, fps: float = 30.0):
        self.text = text
        self.on_packet = on_packet
        self.on_exit = on_exit
        self.fps = fps
        self._stop = threading.Event()
        self._replay = threading.Event()
        self.stopping = False

    def replay(self):
        self._replay.set()

    def start(self):
        self._stop.clear()
        self._replay.set()
        self.stopping = False
        threading.Thread(target=self._run, daemon=True).start()

    def _render(self, lms):
        try:
            import cv2
            import numpy as np
        except ImportError:
            return b''
        img = np.full((480, 640, 3), 236, np.uint8)
        cv2.putText(img, 'SYNTHETIC TEST INPUT - NOT CAMERA', (20, 460), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (90, 90, 90), 1)
        if lms:
            pts = [(int(p[0] * 640), int(p[1] * 480)) for p in lms]
            for a, b in [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
                         (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]:
                cv2.line(img, pts[a], pts[b], (180, 150, 120), 3)
        ok, jpg = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        return jpg.tobytes() if ok else b''

    def _run(self):
        import numpy as np
        from .core.synth import write_number
        from .core.synthetic_hand import hand_landmarks, writing_sequence
        self.on_packet({'type': 'status', 'state': 'opened', 'message': 'Synthetic test source'}, b'')
        seq = 0
        period = 1.0 / self.fps
        idle = hand_landmarks(gestures.OPEN, (0.8, 0.5))
        while not self._stop.is_set():
            frames = []
            if self._replay.is_set():
                self._replay.clear()
                rng = np.random.default_rng(int(time.time()))
                strokes = write_number(self.text, rng, level=0.6, origin=(0.18, 0.28), height=0.34)
                frames = writing_sequence(strokes, fps=self.fps, rng=rng)
            if not frames:
                frames = [(0.0, gestures.OPEN, idle)]
            for _t, _g, lms in frames:
                if self._stop.is_set() or (self._replay.is_set() and len(frames) > 1):
                    break
                seq += 1
                meta = {'type': 'frame', 'seq': seq, 't': time.time(), 'w': 640, 'h': 480,
                        'hands': [{'landmarks': lms, 'handedness': 'synthetic', 'score': 1.0}]}
                self.on_packet(meta, self._render(lms))
                time.sleep(period)
        self.on_exit(self, True, 'Synthetic source stopped')

    def stop(self):
        self.stopping = True
        self._stop.set()


class HandGestureNode(Node):
    def __init__(self):
        super().__init__('hand_gesture_node')
        p = self.declare_parameter
        p('camera_backend', 'windows')
        p('windows_python', 'python.exe')
        p('camera_index', 0)
        p('width', 640)
        p('height', 480)
        p('fps', 30)
        p('jpeg_quality', 75)
        p('model_path', '')
        p('autostart', True)
        p('synthetic_text', '3')
        p('fist_hold_s', 0.8)
        p('down_frames', 3)
        p('up_frames', 3)
        p('lost_timeout_s', 0.35)
        g = lambda n: self.get_parameter(n).value  # noqa: E731

        self.pen = PenTracker(PenConfig(fist_hold_s=float(g('fist_hold_s')), down_frames=int(g('down_frames')),
                                        up_frames=int(g('up_frames')), lost_timeout_s=float(g('lost_timeout_s'))))
        self.gesture_cfg = gestures.GestureConfig()
        self._lock = threading.RLock()
        self._source = None
        self._state = 'off'
        self._state_msg = 'Camera is off'
        self._frame_times = deque(maxlen=60)
        self._last_ink_pub = 0.0
        self._last_ink_rev = -1

        self.pub_img = self.create_publisher(CompressedImage, '/digit_arm/camera/image/compressed', SENSOR)
        self.pub_status = self.create_publisher(CameraStatus, '/digit_arm/camera/status', LATCHED)
        self.pub_hand = self.create_publisher(HandState, '/digit_arm/hand_state', 10)
        self.pub_ink = self.create_publisher(Ink, '/digit_arm/ink', LATCHED)
        self.pub_submit = self.create_publisher(Ink, '/digit_arm/ink/submitted', 10)
        self.create_service(SetBool, '/digit_arm/camera/enable', self._srv_enable)
        self.create_service(Trigger, '/digit_arm/ink/clear', self._srv_clear)
        self.create_timer(1.0, self._publish_status)
        self._publish_ink(force=True)
        self._set_state('off', 'Camera is off')
        if g('autostart'):
            self._start_camera()

    # ── 來源管理 ──────────────────────────────────────────────
    def _worker_args(self):
        g = lambda n: self.get_parameter(n).value  # noqa: E731
        model = g('model_path')
        if not model:
            from ament_index_python.packages import get_package_share_directory
            model = os.path.join(get_package_share_directory('digit_arm'), 'models', 'hand_landmarker.task')
        backend = g('camera_backend')
        model_arg = winproc.to_windows_path(model) if backend == 'windows' else os.path.realpath(model)
        return ['--model', model_arg, '--camera', str(g('camera_index')), '--width', str(g('width')),
                '--height', str(g('height')), '--fps', str(g('fps')), '--jpeg-quality', str(g('jpeg_quality'))]

    def _start_camera(self) -> tuple:
        with self._lock:
            if self._source is not None:
                return True, 'Camera already running'
            backend = self.get_parameter('camera_backend').value
            try:
                if backend == 'windows':
                    py = winproc.resolve_windows_python(self.get_parameter('windows_python').value)
                    script = winproc.to_windows_path(winproc.worker_script('camera_worker.py'))
                    src = WindowsWorkerSource(py, script, self._worker_args(), self._on_packet, self._on_exit,
                                              self.get_logger().info)
                elif backend == 'local':
                    src = LocalSource(self._worker_args(), self._on_packet, self._on_exit, self.get_logger().info)
                elif backend == 'synthetic':
                    src = SyntheticSource(str(self.get_parameter('synthetic_text').value), self._on_packet,
                                          self._on_exit, self.get_logger().info)
                else:
                    raise ValueError(f'Unsupported camera_backend: {backend}')
                self._source = src
                self._set_state('starting', 'Opening camera...')
                src.start()
            except Exception as exc:  # noqa: BLE001
                self._source = None
                self._set_state('error', f'Cannot start camera: {exc}')
                return False, str(exc)
        return True, 'Camera starting'

    def _stop_camera(self):
        with self._lock:
            src, self._source = self._source, None
            self.pen.finish_stroke()
            self._publish_ink(force=True)
        if src is not None:
            src.stop()
        self._set_state('off', 'Camera is off')
        self._publish_hand(None, None)

    def _on_exit(self, src, expected: bool, message: str):
        if not rclpy.ok():
            return
        with self._lock:
            if self._source is not src and self._source is not None:
                return  # 舊來源的結束通知，已經換成新的來源
            self._source = None
            self.pen.finish_stroke()
            self._publish_ink(force=True)
        if expected:
            if self._state != 'off':
                self._set_state('off', 'Camera is off')
        elif self._state != 'error':
            self._set_state('error', message)
        self._publish_hand(None, None)

    # ── 影格處理（工作程式的讀取執行緒） ───────────────────────
    def _on_packet(self, meta: dict, jpeg: bytes):
        if not rclpy.ok():
            return
        try:
            self._handle_packet(meta, jpeg)
        except Exception as exc:  # noqa: BLE001  關閉過程中發布失敗不影響其他節點
            if rclpy.ok():
                self.get_logger().error(f'Frame processing failed: {exc}')

    def _handle_packet(self, meta: dict, jpeg: bytes):
        kind = meta.get('type')
        if kind == 'error':
            self._set_state('error', meta.get('message', 'Camera error'))
            return
        if kind == 'status':
            self._set_state('starting', meta.get('message', 'Camera open, waiting for picture...'))
            return
        if kind != 'frame':
            return
        now = time.monotonic()
        self._frame_times.append(now)
        if self._state != 'streaming':
            self._set_state('streaming', 'Camera running')
        stamp = self.get_clock().now().to_msg()
        if jpeg:
            img = CompressedImage()
            img.header.stamp = stamp
            img.header.frame_id = 'laptop_camera_mirrored'
            img.format = 'jpeg'
            img.data = jpeg
            self.pub_img.publish(img)

        w, h = int(meta.get('w', 640)), int(meta.get('h', 480))
        hands = meta.get('hands') or []
        lms = hands[0]['landmarks'] if hands else None
        t = float(meta.get('t', time.time()))
        with self._lock:
            if self._source is None:
                return
            if lms:
                gesture = gestures.classify(gestures.to_metric(lms, w, h), self.gesture_cfg)
                snap = self.pen.update(t, HandObservation(gesture, (lms[8][0], lms[8][1])))
            else:
                snap = self.pen.update(t, None)
            if 'submitted' in snap.events:
                self._publish_ink(force=True)
                self.pub_submit.publish(self._ink_msg())
                self.get_logger().info(f'Drawing sent for reading (ink_id={self.pen.ink_id}, {len(self.pen.strokes)} strokes)')
            else:
                self._publish_ink(force=bool(snap.events))
        self._publish_hand(snap, lms, stamp)

    # ── 發布 ─────────────────────────────────────────────────
    def _ink_msg(self) -> Ink:
        msg = Ink()
        msg.stamp = self.get_clock().now().to_msg()
        msg.ink_id = self.pen.ink_id
        msg.locked = self.pen.locked
        msg.pen_down = self.pen.pen_down
        msg.interrupted = self.pen.interrupted
        for s in self.pen.all_strokes():
            st = Stroke()
            st.x = [float(p[0]) for p in s]
            st.y = [float(p[1]) for p in s]
            msg.strokes.append(st)
        return msg

    def _publish_ink(self, force: bool = False):
        now = time.monotonic()
        if not force and (self.pen.revision == self._last_ink_rev or now - self._last_ink_pub < 0.05):
            return
        self._last_ink_rev = self.pen.revision
        self._last_ink_pub = now
        self.pub_ink.publish(self._ink_msg())

    def _publish_hand(self, snap, lms, stamp=None):
        msg = HandState()
        msg.stamp = stamp or self.get_clock().now().to_msg()
        if snap is None:
            msg.hand_visible = False
            msg.gesture = gestures.NONE
            msg.pen = 'locked' if self.pen.locked else 'up'
            msg.tip_x = msg.tip_y = -1.0
            msg.message = 'Camera is off' if self._state == 'off' else ''
        else:
            msg.hand_visible = snap.hand_visible
            msg.gesture = snap.gesture
            msg.pen = snap.pen
            msg.fist_progress = float(snap.fist_progress)
            msg.tip_x, msg.tip_y = (float(snap.tip[0]), float(snap.tip[1])) if snap.tip else (-1.0, -1.0)
            msg.message = snap.message
            if lms:
                msg.landmarks_x = [float(p[0]) for p in lms]
                msg.landmarks_y = [float(p[1]) for p in lms]
        self.pub_hand.publish(msg)

    def _fps(self) -> float:
        times = [t for t in self._frame_times if time.monotonic() - t < 2.0]
        if len(times) < 2:
            return 0.0
        return (len(times) - 1) / (times[-1] - times[0])

    def _set_state(self, state: str, message: str):
        self._state = state
        self._state_msg = message
        self._publish_status()

    def _publish_status(self):
        msg = CameraStatus()
        msg.state = self._state
        msg.message = self._state_msg
        src = self._source
        msg.source = getattr(src, 'label', '') if src else ''
        msg.fps = float(self._fps()) if self._state == 'streaming' else 0.0
        self.pub_status.publish(msg)

    # ── 服務 ─────────────────────────────────────────────────
    def _srv_enable(self, req, resp):
        if req.data:
            ok, msg = self._start_camera()
        else:
            self._stop_camera()
            ok, msg = True, 'Camera is off'
        resp.success, resp.message = ok, msg
        return resp

    def _srv_clear(self, _req, resp):
        with self._lock:
            self.pen.clear()
            self._publish_ink(force=True)
            src = self._source
        if isinstance(src, SyntheticSource):
            src.replay()
        resp.success = True
        resp.message = f'Cleared (ink_id={self.pen.ink_id})'
        return resp

    def destroy_node(self):
        src, self._source = self._source, None
        if src is not None:
            src.stop()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = HandGestureNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
