"""movearm 控制器（movearm/arm_controller/arm_controller.ino）的序列協定。

以下全部取自原始碼，沒有新增任何指令：
- 115200 baud，每行一個封包：「底座,大臂,前臂,腕\\n」四個角度（度）。
  movearm.py 送出時把大臂與前臂取負號：f"{a0:.1f},{-a1:.1f},{-a2:.1f},{a3:.1f}\\n"。
- 韌體把前三個數字 × (200×16/360) 步／度，當成「相對開機時姿勢」的絕對步數目標；
  第四個數字寫到 pin 9 的伺服（只有與上次差 > 1° 才寫）。本功能固定送 90（movearm HOME 值）。
- 開機（開啟序列埠會重設 Arduino）後印出「System Ready」。
- 步進馬達由「移動中」變成「停止」時印出一次「OK」；目標與現況相同時不會回覆。
- 沒有狀態查詢、急停、關閉馬達指令；每一步 600 µs 高 + 600 µs 低，約 90°/s，無加速度。
- movearm 介面的 HOME 為 [0, 0, 0, 90]，滑桿範圍：底座 −180～180、大臂 0～140、
  前臂 0～190、腕 0～180（這是程式範圍，不代表實體不會碰撞）。

注意：H、G0、G1、M3、M5、M2 屬於另一份繪圖韌體（drawing_arm_arduino.ino），
arm_controller.ino 不認得它們（沒有逗號的行會被忽略），本程式不會送出。
"""

from __future__ import annotations

from typing import Sequence, Tuple

BAUD_RATE = 115200
BOOT_BANNER = 'System Ready'
DONE_REPLY = 'OK'
# 其他韌體的開機訊息：看到就代表板子上不是 arm_controller，拒絕操作
FOREIGN_BANNERS = ('READY',)

STEPS_PER_DEGREE = 200.0 * 16.0 / 360.0
STEP_PERIOD_S = 0.0012          # 600 µs HIGH + 600 µs LOW
NOMINAL_SPEED_DEG_S = 1.0 / (STEP_PERIOD_S * STEPS_PER_DEGREE)  # ≈ 93.75°/s（未計迴圈額外時間）

JOINTS = ('base', 'arm', 'forearm')
JOINT_LABELS = {'base': 'Base', 'arm': 'Arm', 'forearm': 'Forearm'}
HOME_UI: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 90.0)
UI_LIMITS = {'base': (-180.0, 180.0), 'arm': (0.0, 140.0), 'forearm': (0.0, 190.0)}
WRIST_FIXED = 90.0

Angles = Sequence[float]


def format_packet(ui_angles: Angles) -> str:
    """與 movearm.py send_serial 相同的格式（不含換行）。"""
    a0, a1, a2, a3 = (float(v) for v in ui_angles)
    return f"{a0 + 0.0:.1f},{(-a1) + 0.0:.1f},{(-a2) + 0.0:.1f},{a3 + 0.0:.1f}"


def parse_packet(line: str) -> Tuple[float, float, float, float]:
    """format_packet 的反向（測試與模擬控制器用），回傳 movearm 介面角度。"""
    parts = [float(p) for p in line.strip().split(',')]
    if len(parts) != 4:
        raise ValueError(f'Need 4 numbers: {line!r}')
    return parts[0], -parts[1] + 0.0, -parts[2] + 0.0, parts[3]


def out_pose(joint: str, delta_deg: float) -> Tuple[float, float, float, float]:
    pose = list(HOME_UI)
    pose[JOINTS.index(joint)] += delta_deg
    return tuple(pose)  # type: ignore[return-value]


def move_duration_s(delta_deg: float) -> float:
    return abs(delta_deg) / NOMINAL_SPEED_DEG_S
