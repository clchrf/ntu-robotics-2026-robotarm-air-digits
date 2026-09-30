"""ROS 2 整合檢查（在已 source 工作區的 WSL 終端機執行）。

啟動 launch（合成手勢來源、模擬手臂、不開介面），然後以用戶端驗證：
 1. 合成手勢寫出的數字經 hand_gesture_node → digit_recognizer_node 得到正確結果
 2. 與辨識結果不符的次數會被動作節點拒絕
 3. RepeatMotion 會回報進度並完成
 4. 清除後重寫，執行中取消：不再開始新的一下
 5. 實體模式在未設定動作時拒絕連線、不開序列埠（使用暫存設定：動作清空、假序列埠，絕不碰真的 COM 埠）
執行前若偵測到程式正在執行會直接結束，避免干擾正在使用的手臂。
用法：python3 tools/ros_integration_check.py [數字，預設 3]
"""

import os
import signal
import subprocess
import sys
import time

import rclpy
from rclpy.action import ActionClient
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_srvs.srv import Trigger

from digit_arm_interfaces.action import RepeatMotion
from digit_arm_interfaces.msg import ArmStatus, Ink, RecognitionResult

LATCHED = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST,
                     reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)
TEXT = sys.argv[1] if len(sys.argv) > 1 else '3'
results = []


def check(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name + (f'  — {detail}' if detail else ''), flush=True)


def spin_until(node, pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.05)
        if pred():
            return True
    return False


def launch(*args):
    return subprocess.Popen(['ros2', 'launch', 'digit_arm', 'digit_arm.launch.py', 'ui:=false', *args],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)


def stop(proc):
    proc.send_signal(signal.SIGINT)  # 只通知 launch，由它依序關閉節點
    try:
        out, _ = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
    return out


def app_running() -> bool:
    return subprocess.run(['pgrep', '-f', 'digit_arm/lib/digit_arm'], capture_output=True).returncode == 0


def safe_hardware_config() -> str:
    """實體模式測試用的暫存設定：清空動作設定、改用不存在的假序列埠，保證絕不會開啟真正的 COM 埠。"""
    import tempfile
    import yaml
    from ament_index_python.packages import get_package_share_directory
    src = os.path.join(get_package_share_directory('digit_arm'), 'config', 'digit_arm.yaml')
    cfg = yaml.safe_load(open(src, encoding='utf-8'))
    ex = cfg.setdefault('motion_executor_node', {}).setdefault('ros__parameters', {})
    ex.update({'motion_joint': '', 'motion_delta_deg': 0.0, 'motion_base_deg': 0.0, 'motion_arm_deg': 0.0,
               'motion_forearm_deg': 0.0, 'port': 'loop://', 'auto_connect': False})
    tmp = tempfile.NamedTemporaryFile('w', suffix='_hwtest.yaml', delete=False, encoding='utf-8')
    yaml.safe_dump(cfg, tmp, allow_unicode=True)
    tmp.close()
    return tmp.name


def main():
    if app_running():
        print('偵測到「空中寫數字」正在執行：請先關閉再測試（兩者共用 ROS 名稱，測試會干擾甚至操作到你的手臂）。')
        sys.exit(2)
    rclpy.init()
    node = rclpy.create_node('integration_check')
    state = {'rec': None, 'ink': None, 'arm': None}
    node.create_subscription(RecognitionResult, '/digit_arm/recognition', lambda m: state.update(rec=m), LATCHED)
    node.create_subscription(Ink, '/digit_arm/ink', lambda m: state.update(ink=m), LATCHED)
    node.create_subscription(ArmStatus, '/digit_arm/arm/status', lambda m: state.update(arm=m), LATCHED)
    clear = node.create_client(Trigger, '/digit_arm/ink/clear')
    action = ActionClient(node, RepeatMotion, '/digit_arm/repeat_motion')

    proc = launch('camera_backend:=synthetic', f'synthetic_text:={TEXT}', 'max_count:=20')
    try:
        check('動作 action server 上線', action.wait_for_server(timeout_sec=20))
        ok = spin_until(node, lambda: state['rec'] is not None and state['ink'] is not None
                        and state['rec'].ink_id == state['ink'].ink_id, 40)
        rec = state['rec']
        check('合成手勢 → 辨識結果', ok and rec.success and rec.value == int(TEXT),
              f'success={rec.success if rec else None} value={rec.value if rec else None} reason={rec.reason if rec else ""}')
        check('ArmStatus 標示模擬', state['arm'] is not None and state['arm'].simulated and state['arm'].ready)
        if not (ok and rec.success):
            return

        def send(count, ink_id, cancel_after=None):
            feedback = []
            fut = action.send_goal_async(RepeatMotion.Goal(count=count, ink_id=ink_id),
                                         feedback_callback=lambda f: feedback.append(f.feedback))
            spin_until(node, fut.done, 5)
            gh = fut.result()
            if not gh.accepted:
                return False, None, feedback
            res_fut = gh.get_result_async()
            canceled = False
            end = time.monotonic() + 60
            while not res_fut.done() and time.monotonic() < end:
                rclpy.spin_once(node, timeout_sec=0.05)
                if cancel_after is not None and not canceled and any(f.completed >= cancel_after for f in feedback):
                    gh.cancel_goal_async()
                    canceled = True
            return True, res_fut.result().result, feedback

        accepted, _, _ = send(int(TEXT) + 1, rec.ink_id)
        check('與辨識結果不符的次數被拒絕', not accepted)
        accepted, _, _ = send(int(TEXT), rec.ink_id + 7)
        check('舊的 ink_id 被拒絕', not accepted)

        accepted, res, fb = send(int(TEXT), rec.ink_id)
        done_counts = sorted({f.completed for f in fb})
        check('動作完成並回報進度', accepted and res.outcome == 'succeeded' and res.completed == int(TEXT)
              and res.simulated and done_counts[-1] == int(TEXT),
              f'outcome={res.outcome if res else None} 進度={done_counts} 訊息={res.message if res else ""}')

        # 清除 → 合成來源重寫 → 執行中取消
        f = clear.call_async(Trigger.Request())
        spin_until(node, f.done, 5)
        old = rec.ink_id
        ok = spin_until(node, lambda: state['rec'] is not None and state['rec'].ink_id > old
                        and state['ink'].ink_id == state['rec'].ink_id, 40)
        rec2 = state['rec']
        check('清除後重新書寫並辨識', ok and rec2.success, rec2.reason if rec2 else '')
        if ok and rec2.success and rec2.value >= 2:
            accepted, res, fb = send(int(rec2.value), rec2.ink_id, cancel_after=1)
            phases = [x.phase for x in fb]
            after_cancel = phases[phases.index('cancel_return') + 1:] if 'cancel_return' in phases else []
            check('執行中取消：不再開始新的一下', accepted and res.outcome == 'canceled'
                  and 1 <= res.completed < rec2.value and 'out' not in after_cancel,
                  f'outcome={res.outcome if res else None} completed={res.completed if res else None} '
                  f'最後階段={phases[-3:]} 訊息={res.message if res else ""}')
    finally:
        out = stop(proc)
        with open('/tmp/digit_arm_integration_launch.log', 'w') as fh:
            fh.write(out)

    # 實體模式：未設定動作 → 拒絕
    state['arm'] = None
    proc = launch('mode:=hardware', 'camera_backend:=synthetic', f'config:={safe_hardware_config()}')
    try:
        connect = node.create_client(Trigger, '/digit_arm/arm/connect')
        connect.wait_for_service(timeout_sec=20)
        fut = connect.call_async(Trigger.Request())
        spin_until(node, fut.done, 10)
        r = fut.result()
        spin_until(node, lambda: state['arm'] is not None and state['arm'].mode == 'hardware', 5)
        check('實體模式未設定動作時拒絕連線', r is not None and not r.success and 'No motion set' in r.message,
              r.message if r else '')
        check('實體模式未連線時不可接受目標', state['arm'] is not None and not state['arm'].ready)
    finally:
        stop(proc)

    node.destroy_node()
    rclpy.shutdown()
    failed = [n for n, ok in results if not ok]
    print(f'\n{len(results) - len(failed)}/{len(results)} 通過' + (f'；失敗：{failed}' if failed else ''))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
