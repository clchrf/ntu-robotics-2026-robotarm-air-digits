"""PyQt 與 ROS 之間的橋接。

- rclpy 在背景執行緒 spin；PyQt 主執行緒絕不等待 ROS。
- 介面要做的 ROS 呼叫（服務、action）先放進佇列，由 guard condition 喚醒 ROS 執行緒執行。
- 所有回呼只把資料轉成 Python dict，透過 Qt signal（跨執行緒自動排隊）交給主執行緒。
- 影像只保留最新一張：主執行緒還沒處理完就不再送，避免延遲堆積。
"""

from __future__ import annotations

import queue
import threading
from typing import Callable

import rclpy
from action_msgs.msg import GoalStatus
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage
from rclpy.action import ActionClient
from rclpy.executors import SingleThreadedExecutor
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage
from std_srvs.srv import SetBool, Trigger

from digit_arm_interfaces.action import RepeatMotion
from digit_arm_interfaces.msg import ArmStatus, CameraStatus, HandState, Ink, RecognitionResult

LATCHED = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST,
                     reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)
SENSOR = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.BEST_EFFORT)


class BridgeSignals(QObject):
    frame = pyqtSignal()
    hand = pyqtSignal(dict)
    ink = pyqtSignal(dict)
    recognition = pyqtSignal(dict)
    camera_status = pyqtSignal(dict)
    arm_status = pyqtSignal(dict)
    executor_online = pyqtSignal(bool)
    motion_accepted = pyqtSignal()
    motion_rejected = pyqtSignal(str)
    motion_feedback = pyqtSignal(dict)
    motion_done = pyqtSignal(dict)
    service_result = pyqtSignal(str, bool, str)


class RosBridge(BridgeSignals):
    def __init__(self, args=None):
        super().__init__()
        rclpy.init(args=args)
        self.node = rclpy.create_node('digit_arm_ui')
        n = self.node
        self._latest_frame = None
        self._frame_lock = threading.Lock()
        self._frame_pending = False
        self._goal_handle = None
        self._calls: 'queue.Queue[Callable[[], None]]' = queue.Queue()
        self._guard = n.create_guard_condition(self._drain_calls)

        n.create_subscription(CompressedImage, '/digit_arm/camera/image/compressed', self._on_image, SENSOR)
        n.create_subscription(HandState, '/digit_arm/hand_state', self._on_hand, 10)
        n.create_subscription(Ink, '/digit_arm/ink', self._on_ink, LATCHED)
        n.create_subscription(RecognitionResult, '/digit_arm/recognition', self._on_rec, LATCHED)
        n.create_subscription(CameraStatus, '/digit_arm/camera/status', self._on_cam, LATCHED)
        n.create_subscription(ArmStatus, '/digit_arm/arm/status', self._on_arm, LATCHED)
        self.cli_camera = n.create_client(SetBool, '/digit_arm/camera/enable')
        self.cli_clear = n.create_client(Trigger, '/digit_arm/ink/clear')
        self.cli_connect = n.create_client(Trigger, '/digit_arm/arm/connect')
        self.action = ActionClient(n, RepeatMotion, '/digit_arm/repeat_motion')
        self._online = None
        n.create_timer(0.5, self._check_online)

        self._executor = SingleThreadedExecutor()
        self._executor.add_node(n)
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self):
        try:
            self._executor.spin()
        except Exception:  # noqa: BLE001  關閉時的例外
            pass

    # ── 主執行緒 → ROS 執行緒 ────────────────────────────────
    def _post(self, fn: Callable[[], None]):
        self._calls.put(fn)
        self._guard.trigger()

    def _drain_calls(self):
        while True:
            try:
                fn = self._calls.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                self.node.get_logger().error(f'UI call failed: {exc}')

    def _call_service(self, name: str, client, request):
        def run():
            if not client.service_is_ready():
                self.service_result.emit(name, False, 'No reply from that node (is the launch running?)')
                return
            fut = client.call_async(request)

            def done(f):
                try:
                    r = f.result()
                    self.service_result.emit(name, bool(r.success), r.message)
                except Exception as exc:  # noqa: BLE001
                    self.service_result.emit(name, False, str(exc))
            fut.add_done_callback(done)
        self._post(run)

    def set_camera(self, on: bool):
        self._call_service('camera', self.cli_camera, SetBool.Request(data=on))

    def clear_ink(self):
        self._call_service('clear', self.cli_clear, Trigger.Request())

    def connect_arm(self):
        self._call_service('connect', self.cli_connect, Trigger.Request())

    def start_motion(self, count: int, ink_id: int):
        def run():
            if not self.action.server_is_ready():
                self.motion_rejected.emit('Motion node is not replying')
                return
            goal = RepeatMotion.Goal(count=int(count), ink_id=int(ink_id))
            fut = self.action.send_goal_async(goal, feedback_callback=self._on_feedback)
            fut.add_done_callback(self._on_goal_response)
        self._post(run)

    def cancel_motion(self):
        def run():
            if self._goal_handle is not None:
                self._goal_handle.cancel_goal_async()
        self._post(run)

    def take_frame(self):
        with self._frame_lock:
            img, self._latest_frame = self._latest_frame, None
            self._frame_pending = False
        return img

    def shutdown(self):
        try:
            self._executor.shutdown(timeout_sec=1.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.node.destroy_node()
        except Exception:  # noqa: BLE001
            pass
        if rclpy.ok():
            rclpy.shutdown()

    # ── ROS 回呼（ROS 執行緒） ───────────────────────────────
    def _on_goal_response(self, fut):
        try:
            handle = fut.result()
        except Exception as exc:  # noqa: BLE001
            self.motion_rejected.emit(str(exc))
            return
        if not handle.accepted:
            self.motion_rejected.emit('Motion node said no')
            return
        self._goal_handle = handle
        self.motion_accepted.emit()
        handle.get_result_async().add_done_callback(self._on_result)

    def _on_result(self, fut):
        self._goal_handle = None
        try:
            res = fut.result()
        except Exception as exc:  # noqa: BLE001
            self.motion_done.emit({'outcome': 'aborted', 'completed': 0, 'requested': 0,
                                   'simulated': False, 'message': f'Could not read the result: {exc}'})
            return
        r = res.result
        outcome = r.outcome or {GoalStatus.STATUS_SUCCEEDED: 'succeeded',
                                GoalStatus.STATUS_CANCELED: 'canceled'}.get(res.status, 'aborted')
        self.motion_done.emit({'outcome': outcome, 'completed': r.completed, 'requested': r.requested,
                               'simulated': r.simulated, 'message': r.message or ''})

    def _on_feedback(self, fb_msg):
        fb = fb_msg.feedback
        self.motion_feedback.emit({'completed': fb.completed, 'total': fb.total, 'phase': fb.phase,
                                   'simulated': fb.simulated})

    def _on_image(self, msg: CompressedImage):
        img = QImage.fromData(bytes(msg.data))
        if img.isNull():
            return
        with self._frame_lock:
            self._latest_frame = img
            if self._frame_pending:
                return
            self._frame_pending = True
        self.frame.emit()

    def _on_hand(self, m: HandState):
        self.hand.emit({'hand_visible': m.hand_visible, 'gesture': m.gesture, 'pen': m.pen,
                        'fist_progress': m.fist_progress, 'tip': (m.tip_x, m.tip_y),
                        'landmarks_x': list(m.landmarks_x), 'landmarks_y': list(m.landmarks_y),
                        'message': m.message})

    def _on_ink(self, m: Ink):
        self.ink.emit({'ink_id': m.ink_id, 'locked': m.locked, 'pen_down': m.pen_down,
                       'strokes': [list(zip(s.x, s.y)) for s in m.strokes]})

    def _on_rec(self, m: RecognitionResult):
        self.recognition.emit({
            'ink_id': m.ink_id, 'success': m.success, 'value': m.value, 'text': m.text,
            'confidence': m.confidence, 'reason': m.reason,
            'digits': [{'label': d.label, 'confidence': d.confidence, 'runner_up': d.runner_up,
                        'x_min': d.x_min, 'x_max': d.x_max, 'y_min': d.y_min, 'y_max': d.y_max,
                        'stroke_indices': list(d.stroke_indices)} for d in m.digits]})

    def _on_cam(self, m: CameraStatus):
        self.camera_status.emit({'state': m.state, 'source': m.source, 'message': m.message, 'fps': m.fps})

    def _on_arm(self, m: ArmStatus):
        self.arm_status.emit({'mode': m.mode, 'simulated': m.simulated, 'connected': m.connected,
                              'ready': m.ready, 'busy': m.busy, 'position_trusted': m.position_trusted,
                              'port': m.port, 'motion_description': m.motion_description,
                              'max_count': m.max_count, 'message': m.message})

    def _check_online(self):
        online = self.action.server_is_ready() and self.node.count_publishers('/digit_arm/arm/status') > 0
        if online != self._online:
            self._online = online
            self.executor_online.emit(online)
