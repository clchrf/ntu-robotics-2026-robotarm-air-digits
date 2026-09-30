"""動作執行節點：接收使用者確認的次數，重複固定小幅動作，回報進度、可取消。

Action  /digit_arm/repeat_motion  RepeatMotion（回饋 completed/total/phase；可取消）
Topic   /digit_arm/arm/status     ArmStatus（latched）
Service /digit_arm/arm/connect    std_srvs/Trigger（實體模式：連線，開埠會重設 Arduino）
        /digit_arm/arm/disconnect std_srvs/Trigger

mode
  simulation — 只計時，不開序列埠（預設；所有訊息標示「模擬」）
  hardware   — movearm arm_controller 協定；motion_joint / motion_delta_deg 未設定就拒絕連線

安全檢查（目標被拒絕時 ArmStatus.message 會說明原因）
- 次數必須在 1..max_count，且等於「目前筆跡」最新一次成功辨識的值。
- 同時只執行一個目標；實體模式需已連線且位置可信。
"""

from __future__ import annotations

import threading
import time

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_srvs.srv import Trigger

from digit_arm_interfaces.action import RepeatMotion
from digit_arm_interfaces.msg import ArmStatus, Ink, RecognitionResult

from .core import movearm_protocol as mp
from .core import winproc
from .core.motion import (BackendError, MotionSettings, MovearmSerialArm, RepeatRunner, SimulatedArm,
                          validate_hardware_settings)
from .core.serial_link import PySerialLink, WindowsBridgeLink

LATCHED = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST,
                     reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)


class MotionExecutorNode(Node):
    def __init__(self):
        super().__init__('motion_executor_node')
        p = self.declare_parameter
        p('mode', 'simulation')
        p('max_count', 20)
        p('port', 'win:auto')
        p('baud', mp.BAUD_RATE)
        p('windows_python', 'python.exe')
        p('motion_joint', '')
        p('motion_delta_deg', 0.0)
        p('motion_base_deg', 0.0)
        p('motion_arm_deg', 0.0)
        p('motion_forearm_deg', 0.0)
        p('dwell_s', 0.3)
        p('pause_s', 0.25)
        p('boot_timeout_s', 5.0)
        p('auto_connect', False)
        p('sim_joint', 'base')
        p('sim_delta_deg', 10.0)
        p('smooth_motion', True)
        p('stream_hz', 125.0)
        p('max_speed_deg_s', 25.0)
        p('max_step_deg', 0.25)
        p('firmware_max_speed_deg_s', 0.0)
        p('firmware_accel_deg_s2', 0.0)
        p('allow_beyond_movearm_limits', False)
        p('max_delta_deg', 45.0)
        g = lambda n: self.get_parameter(n).value  # noqa: E731

        self.mode = str(g('mode'))
        self.max_count = int(g('max_count'))
        self._cb = ReentrantCallbackGroup()
        self._lock = threading.Lock()
        self._busy = False
        self._connecting = False
        self._message = ''
        self._shutting_down = False
        self._current_ink = None
        self._recognition = None

        if self.mode == 'hardware':
            self.settings = MotionSettings(joint=str(g('motion_joint')), delta_deg=float(g('motion_delta_deg')),
                                           deltas=self._deltas(),
                                           dwell_s=float(g('dwell_s')), pause_s=float(g('pause_s')),
                                           **self._smooth_params())
            self.backend = MovearmSerialArm(self._make_link, self.settings,
                                            boot_timeout_s=float(g('boot_timeout_s')),
                                            log=self.get_logger().info)
            errs = validate_hardware_settings(self.settings)
            self._message = ('Hardware locked: ' + '; '.join(errs)) if errs else \
                'Not connected. Put the arm at the start pose, then press "Connect arm".'
        elif self.mode == 'simulation':
            joint = str(g('motion_joint')) or str(g('sim_joint'))
            delta = float(g('motion_delta_deg')) or float(g('sim_delta_deg'))
            self.settings = MotionSettings(joint=joint, delta_deg=delta, deltas=self._deltas(),
                                           dwell_s=float(g('dwell_s')), pause_s=float(g('pause_s')),
                                           **self._smooth_params())
            self.backend = SimulatedArm(self.settings)
            self._message = 'Simulation: the arm will not move'
        else:
            raise ValueError(f'mode must be simulation or hardware, got {self.mode!r}')

        self.pub_status = self.create_publisher(ArmStatus, '/digit_arm/arm/status', LATCHED)
        self.create_subscription(Ink, '/digit_arm/ink', self._on_ink, LATCHED, callback_group=self._cb)
        self.create_subscription(RecognitionResult, '/digit_arm/recognition', self._on_recognition, LATCHED,
                                 callback_group=self._cb)
        self.create_service(Trigger, '/digit_arm/arm/connect', self._srv_connect, callback_group=self._cb)
        self.create_service(Trigger, '/digit_arm/arm/disconnect', self._srv_disconnect, callback_group=self._cb)
        self._action = ActionServer(self, RepeatMotion, '/digit_arm/repeat_motion',
                                    execute_callback=self._execute, goal_callback=self._on_goal,
                                    cancel_callback=self._on_cancel, callback_group=self._cb)
        self.create_timer(0.5, self._publish_status, callback_group=self._cb)
        self._publish_status()
        self.get_logger().info(f'Motion node ready: {self.mode}, {self.settings.describe()}, max {self.max_count}')
        if self.mode == 'hardware' and g('auto_connect'):
            threading.Thread(target=self._connect, daemon=True).start()

    def _deltas(self):
        g = lambda n: self.get_parameter(n).value  # noqa: E731
        return (float(g('motion_base_deg')), float(g('motion_arm_deg')), float(g('motion_forearm_deg')))

    def _smooth_params(self) -> dict:
        g = lambda n: self.get_parameter(n).value  # noqa: E731
        return {'smooth': bool(g('smooth_motion')), 'stream_hz': float(g('stream_hz')),
                'max_speed_deg_s': float(g('max_speed_deg_s')), 'max_delta_deg': float(g('max_delta_deg')),
                'max_step_deg': float(g('max_step_deg')),
                'fw_max_speed_deg_s': float(g('firmware_max_speed_deg_s')),
                'fw_accel_deg_s2': float(g('firmware_accel_deg_s2')),
                'allow_beyond_movearm_limits': bool(g('allow_beyond_movearm_limits'))}

    # ── 硬體連線 ─────────────────────────────────────────────
    def _make_link(self):
        port = str(self.get_parameter('port').value)
        baud = int(self.get_parameter('baud').value)
        if port.startswith('win:'):
            py = winproc.resolve_windows_python(str(self.get_parameter('windows_python').value))
            script = winproc.to_windows_path(winproc.worker_script('serial_bridge.py'))
            return WindowsBridgeLink(py, script, port[4:] or 'auto', baud, log=self.get_logger().info)
        return PySerialLink(port, baud, log=self.get_logger().info)

    def _connect(self):
        with self._lock:
            if self._busy or self._connecting:
                return False, 'Busy. Cannot connect now.'
            self._connecting = True
        self._message = 'Connecting... (opening the port restarts the controller)'
        self._publish_status()
        try:
            self.backend.connect()
            self._message = self.backend.status_message
            ok = True
        except (BackendError, FileNotFoundError, OSError) as exc:
            self._message = f'Could not connect: {exc}'
            ok = False
        finally:
            self._connecting = False
        self._publish_status()
        return ok, self._message

    def _srv_connect(self, _req, resp):
        if self.mode != 'hardware':
            resp.success, resp.message = True, 'Simulation does not need a connection'
            return resp
        resp.success, resp.message = self._connect()
        return resp

    def _srv_disconnect(self, _req, resp):
        if self._busy:
            resp.success, resp.message = False, 'The arm is moving. Cancel first.'
            return resp
        if self.mode == 'hardware':
            self.backend.close()
            self._message = 'Disconnected'
        self._publish_status()
        resp.success, resp.message = True, self._message
        return resp

    # ── 狀態 ────────────────────────────────────────────────
    def _on_ink(self, msg: Ink):
        self._current_ink = msg.ink_id

    def _on_recognition(self, msg: RecognitionResult):
        self._recognition = msg

    def _ready(self) -> bool:
        if self._busy or self._connecting:
            return False
        if self.mode == 'hardware':
            return self.backend.connected and self.backend.position_trusted
        return True

    def _publish_status(self):
        msg = ArmStatus()
        msg.mode = self.mode
        msg.simulated = self.backend.simulated
        msg.connected = bool(self.backend.connected)
        msg.busy = self._busy
        msg.position_trusted = bool(self.backend.position_trusted)
        msg.ready = self._ready()
        msg.port = self.backend.port_description
        msg.motion_description = self.settings.describe()
        msg.max_count = self.max_count
        if self.mode == 'hardware' and not self._busy and not self._connecting:
            backend_msg = self.backend.status_message  # 會偵測閒置時的斷線
            if not self.backend.connected and self._message.startswith('Connected'):
                self._message = backend_msg
        msg.message = self._message
        self.pub_status.publish(msg)

    # ── Action ──────────────────────────────────────────────
    def _reject(self, reason: str):
        self.get_logger().warn(f'Goal rejected: {reason}')
        self._message = f'Not started: {reason}'
        self._publish_status()
        return GoalResponse.REJECT

    def _on_goal(self, goal: RepeatMotion.Goal):
        n = int(goal.count)
        if self._busy:
            return self._reject('another motion is running')
        if not self._ready():
            return self._reject('arm not ready (' + (self.backend.status_message or 'not connected') + ')')
        if not 1 <= n <= self.max_count:
            return self._reject(f'count {n} is not between 1 and {self.max_count}')
        rec = self._recognition
        if rec is None or rec.ink_id != goal.ink_id or not rec.success or int(rec.value) != n:
            return self._reject('count does not match the current result')
        if self._current_ink is not None and self._current_ink != goal.ink_id:
            return self._reject('the drawing was cleared or changed')
        with self._lock:
            if self._busy:
                return self._reject('another motion is running')
            self._busy = True
        return GoalResponse.ACCEPT

    def _on_cancel(self, _goal_handle):
        self.get_logger().info('Cancel received: no new reps, going back to start')
        return CancelResponse.ACCEPT

    def _execute(self, goal_handle):
        n = int(goal_handle.request.count)
        sim = self.backend.simulated
        self._message = f'Moving: 0 of {n}' + (' (simulation)' if sim else '')
        self._publish_status()
        self.get_logger().info(f'Start {n} reps ({self.settings.describe()})' + (' [simulation]' if sim else ' [hardware]'))

        def progress(done, total, phase):
            fb = RepeatMotion.Feedback()
            fb.completed, fb.total, fb.phase, fb.simulated = done, total, phase, sim
            try:
                goal_handle.publish_feedback(fb)
            except Exception:  # noqa: BLE001  關閉中發布失敗，不可中斷回到起點的流程
                pass
            self._message = f'Moving: {done} of {total}' + (' (simulation)' if sim else '')

        runner = RepeatRunner(self.backend, self.settings)
        try:
            res = runner.run(n, lambda: goal_handle.is_cancel_requested or self._shutting_down, progress)
        finally:
            with self._lock:
                self._busy = False
        result = RepeatMotion.Result()
        result.outcome, result.completed, result.requested = res.outcome, res.completed, res.requested
        result.simulated, result.message = sim, res.message
        if res.outcome == 'succeeded':
            goal_handle.succeed()
        elif res.outcome == 'canceled' and goal_handle.is_cancel_requested:
            goal_handle.canceled()
        else:
            goal_handle.abort()
        self._message = res.message
        self.get_logger().info(res.message)
        self._publish_status()
        return result

    def shutdown(self):
        self._shutting_down = True
        deadline = time.monotonic() + 5.0
        while self._busy and time.monotonic() < deadline:
            time.sleep(0.05)
        self.backend.close()


def main(args=None):
    rclpy.init(args=args)
    node = MotionExecutorNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    spin = threading.Thread(target=executor.spin, daemon=True)
    spin.start()
    try:
        while rclpy.ok() and spin.is_alive():
            spin.join(0.2)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.shutdown()
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
