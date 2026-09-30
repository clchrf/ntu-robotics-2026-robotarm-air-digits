"""實際鏡頭檢查（WSL）：hand_gesture_node 以 Windows 工作程式開啟筆電鏡頭。

量測影像與手部狀態頻率、開關鏡頭服務，並確認關閉後 Windows 端的 camera_worker 已結束。
不會自動判斷手勢是否正確；若測試時把手放進畫面，會列出看到的手勢。
用法：python3 tools/camera_check.py [秒數，預設 8]
"""

import collections
import os
import signal
import subprocess
import sys
import time

import rclpy
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage
from std_srvs.srv import SetBool

from digit_arm_interfaces.msg import CameraStatus, HandState

LATCHED = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST,
                     reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)
SENSOR = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.BEST_EFFORT)
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0


def windows_workers() -> int:
    script = ("@(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and "
              "$_.CommandLine -like '*camera_worker.py*' }).Count")
    out = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script], capture_output=True, text=True)
    try:
        return int(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print('  （無法查詢 Windows 行程：' + (out.stderr.strip()[:200] or out.stdout.strip()[:200]) + '）')
        return -1


def spin_for(node, seconds, pred=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.05)
        if pred and pred():
            return True
    return False


def main():
    if subprocess.run(['pgrep', '-f', 'digit_arm/lib/digit_arm'], capture_output=True).returncode == 0:
        print('偵測到「空中寫數字」正在執行：請先關閉再測試。')
        sys.exit(2)
    proc = subprocess.Popen(['ros2', 'run', 'digit_arm', 'hand_gesture_node', '--ros-args',
                             '-p', 'camera_backend:=windows'], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                            start_new_session=True)
    rclpy.init()
    node = rclpy.create_node('camera_check')
    frames, hands, status = [], collections.Counter(), {}
    sizes = []
    node.create_subscription(CompressedImage, '/digit_arm/camera/image/compressed',
                             lambda m: (frames.append(time.monotonic()), sizes.append(len(m.data))), SENSOR)
    node.create_subscription(HandState, '/digit_arm/hand_state',
                             lambda m: hands.update([m.gesture if m.hand_visible else 'none']), 10)
    node.create_subscription(CameraStatus, '/digit_arm/camera/status',
                             lambda m: status.update(state=m.state, message=m.message, fps=m.fps), LATCHED)
    enable = node.create_client(SetBool, '/digit_arm/camera/enable')
    ok = True
    try:
        streaming = spin_for(node, 30, lambda: status.get('state') == 'streaming')
        print(f'鏡頭狀態：{status}')
        ok &= streaming
        t0 = len(frames)
        spin_for(node, SECONDS)
        recent = frames[t0:]
        fps = (len(recent) - 1) / (recent[-1] - recent[0]) if len(recent) > 1 else 0
        print(f'影像：{len(recent)} 張／{SECONDS:g} 秒 ≈ {fps:.1f} fps，JPEG 平均 {sum(sizes) / max(len(sizes), 1) / 1024:.0f} KB')
        print(f'手部狀態（每格）：{dict(hands)}')
        ok &= fps > 10
        print(f'Windows camera_worker 行程數（開啟中）：{windows_workers()}')

        enable.wait_for_service(timeout_sec=5)
        fut = enable.call_async(SetBool.Request(data=False))
        spin_for(node, 10, fut.done)
        spin_for(node, 2, lambda: status.get('state') == 'off')
        print(f'關閉鏡頭：{fut.result().message if fut.done() else "逾時"}，狀態={status.get("state")}')
        time.sleep(1.0)
        n = windows_workers()
        print(f'Windows camera_worker 行程數（關閉後）：{n}')
        ok &= status.get('state') == 'off' and n == 0

        fut = enable.call_async(SetBool.Request(data=True))
        spin_for(node, 10, fut.done)
        reopened = spin_for(node, 30, lambda: status.get('state') == 'streaming')
        print(f'重新開啟鏡頭：{"成功" if reopened else "失敗"}（{status}）')
        ok &= reopened
    finally:
        os.killpg(proc.pid, signal.SIGINT)  # ros2 run 不會轉發訊號，對整個行程群組送一次
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
        node.destroy_node()
        rclpy.shutdown()
    time.sleep(1.0)
    left = windows_workers()
    print(f'節點結束後 Windows camera_worker 行程數：{left}')
    ok &= left == 0
    print('結果：' + ('通過' if ok else '未通過'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
