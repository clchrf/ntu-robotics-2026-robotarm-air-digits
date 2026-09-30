"""介面截圖（不需 ROS）：以假的 bridge 餵入狀態，輸出到 artifacts/ui-*.png。

用法（WSL）：QT_QPA_PLATFORM=offscreen python3 tools/ui_screenshots.py
影像是合成的示意畫面，不是鏡頭拍到的內容。
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'ros2_ws', 'src', 'digit_arm'))

import numpy as np  # noqa: E402
from PyQt6.QtCore import QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter  # noqa: E402

from digit_arm.core.synth import write_number  # noqa: E402
from digit_arm.core.synthetic_hand import hand_landmarks  # noqa: E402
from digit_arm.ui.app import MainWindow, create_app  # noqa: E402
from digit_arm.ui.ros_bridge import BridgeSignals  # noqa: E402

OUT = os.path.join(HERE, '..', 'artifacts')


class FakeBridge(BridgeSignals):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.frame_img = None

    def take_frame(self):
        return self.frame_img

    def set_camera(self, on):
        self.calls.append(('camera', on))

    def clear_ink(self):
        self.calls.append(('clear',))

    def connect_arm(self):
        self.calls.append(('connect',))

    def start_motion(self, count, ink_id):
        self.calls.append(('start', count, ink_id))

    def cancel_motion(self):
        self.calls.append(('cancel',))


def fake_frame():
    img = QImage(640, 480, QImage.Format.Format_RGB32)
    p = QPainter(img)
    g = QLinearGradient(0, 0, 0, 480)
    g.setColorAt(0, QColor('#8a9bab'))
    g.setColorAt(1, QColor('#4b5663'))
    p.fillRect(img.rect(), g)
    p.setBrush(QColor('#c7a58f'))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(170, 170), 70, 88)
    p.setBrush(QColor('#3e4a57'))
    p.drawRoundedRect(60, 280, 230, 220, 60, 60)
    p.end()
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    app = create_app(sys.argv)
    bridge = FakeBridge()
    win = MainWindow(bridge)
    rng = np.random.default_rng(4)
    strokes = [s.tolist() for s in write_number('10', rng, level=0.5, origin=(0.42, 0.2), height=0.36)]
    tip = strokes[-1][len(strokes[-1]) // 2]
    lm = hand_landmarks('point', tip)

    def hand(g, pen, prog=0.0):
        return {'hand_visible': True, 'gesture': g, 'pen': pen, 'fist_progress': prog, 'tip': tuple(tip),
                'landmarks_x': [p[0] for p in lm], 'landmarks_y': [p[1] for p in lm], 'message': ''}

    arm_sim = {'mode': 'simulation', 'simulated': True, 'connected': True, 'ready': True, 'busy': False,
               'position_trusted': True, 'port': 'Simulation', 'motion_description': 'Arm +19°, Forearm +15°, then back',
               'max_count': 20, 'message': 'Simulation: the arm will not move'}
    bridge.camera_status.emit({'state': 'streaming', 'source': 'Windows laptop camera', 'message': '', 'fps': 29.6})
    bridge.executor_online.emit(True)
    bridge.arm_status.emit(arm_sim)
    bridge.frame_img = fake_frame()
    bridge.frame.emit()

    def shot(name, w, h):
        win.resize(w, h)
        win.show()
        app.processEvents()
        win.grab().save(os.path.join(OUT, f'ui-{name}.png'))
        print('saved', name, w, h)

    # 1. 書寫中
    bridge.ink.emit({'ink_id': 5, 'locked': False, 'pen_down': True, 'strokes': strokes})
    bridge.hand.emit(hand('point', 'down'))
    shot('writing-wide', 1280, 820)
    # 2. 握拳送出中
    bridge.hand.emit(hand('fist', 'up', 0.55))
    shot('fist-wide', 1280, 820)
    # 3. 辨識成功
    digits = [{'label': '1', 'confidence': 0.99, 'runner_up': '7', 'x_min': min(p[0] for p in strokes[0]),
               'x_max': max(p[0] for p in strokes[0]), 'y_min': min(p[1] for p in strokes[0]),
               'y_max': max(p[1] for p in strokes[0]), 'stroke_indices': [0]},
              {'label': '0', 'confidence': 0.97, 'runner_up': '6', 'x_min': min(p[0] for p in strokes[1]),
               'x_max': max(p[0] for p in strokes[1]), 'y_min': min(p[1] for p in strokes[1]),
               'y_max': max(p[1] for p in strokes[1]), 'stroke_indices': [1]}]
    bridge.ink.emit({'ink_id': 5, 'locked': True, 'pen_down': False, 'strokes': strokes})
    bridge.hand.emit(hand('open', 'locked'))
    bridge.recognition.emit({'ink_id': 5, 'success': True, 'value': 10, 'text': '10', 'confidence': 0.97,
                             'reason': '', 'digits': digits})
    shot('result-wide', 1280, 820)
    assert win.btn_start.isEnabled() and win.btn_start.text() == 'Start (10 times)  →'
    assert not win.btn_cancel.isEnabled()
    win.btn_start.click()
    assert bridge.calls[-1] == ('start', 10, 5), bridge.calls
    # 4. 執行中
    bridge.motion_accepted.emit()
    bridge.motion_feedback.emit({'completed': 3, 'total': 10, 'phase': 'return', 'simulated': True})
    shot('running-wide', 1280, 820)
    assert win.btn_cancel.isEnabled() and not win.btn_start.isEnabled() and not win.btn_clear.isEnabled()
    shot('running-narrow', 760, 1000)
    shot('running-small', 600, 540)
    win.btn_cancel.click()
    assert bridge.calls[-1] == ('cancel',)
    bridge.motion_done.emit({'outcome': 'canceled', 'completed': 3, 'requested': 10, 'simulated': True,
                             'message': 'Canceled and back at start (simulation): 3 of 10 done'})
    shot('canceled-wide', 1280, 820)
    assert win.btn_clear.isEnabled() and not win.btn_start.isEnabled()
    # 5. 辨識失敗
    bridge.ink.emit({'ink_id': 6, 'locked': True, 'pen_down': False, 'strokes': strokes[:1]})
    bridge.recognition.emit({'ink_id': 6, 'success': False, 'value': 0, 'text': '', 'confidence': 0.55,
                             'reason': 'Not sure about this digit (looks like 1 or 7). Please clear and rewrite.', 'digits': []})
    shot('failed-wide', 1280, 820)
    assert not win.btn_start.isEnabled() and win.btn_clear.isEnabled()
    assert win.progress_text.text() == '— / —', '新筆跡不應顯示上一輪進度'
    # 6. 實體模式未連線 + 鏡頭錯誤
    bridge.arm_status.emit({'mode': 'hardware', 'simulated': False, 'connected': False, 'ready': False,
                            'busy': False, 'position_trusted': False, 'port': '', 'motion_description': 'No motion set',
                            'max_count': 20, 'message': 'Hardware locked: No motion set'})
    bridge.camera_status.emit({'state': 'error', 'source': '', 'fps': 0.0,
                               'message': 'Cannot open camera 0. Another app may be using it, or access is blocked.'})
    bridge.ink.emit({'ink_id': 7, 'locked': False, 'pen_down': False, 'strokes': []})
    shot('hardware-camera-error', 1280, 820)
    assert win.btn_connect.isVisible() and win.btn_connect.isEnabled()
    print('所有介面狀態檢查通過')


if __name__ == '__main__':
    main()
