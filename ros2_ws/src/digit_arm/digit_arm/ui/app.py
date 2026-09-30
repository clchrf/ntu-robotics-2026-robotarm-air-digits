"""空中寫數字 — PyQt6 操作介面（ROS 2 節點 digit_arm_ui）。"""

from __future__ import annotations

import os
import signal
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QBoxLayout, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                             QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from . import style as S
from .state import CANCELLING, RESULT_FAIL, RESULT_OK, RUNNING, UiState
from .widgets import Card, HintRow, InkView, Pill, RoundedProgress, VideoView

NARROW_WIDTH = 1000


def _float_shadow(widget):
    """淡淡的漂浮陰影（0 4px 20px 5% 黑）。"""
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(24)
    eff.setOffset(0, 4)
    eff.setColor(QColor(15, 23, 42, 22))
    widget.setGraphicsEffect(eff)
SIDE_WIDTH = 390


class MainWindow(QMainWindow):
    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge
        self.st = UiState()
        self.setWindowTitle('Air Digits · Arm Counter')
        self.setMinimumSize(560, 520)
        self.resize(1280, 820)
        self._narrow = None
        self._build()
        self._connect_bridge()
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._on_cancel)
        self._refresh()

    # ── 版面 ────────────────────────────────────────────────
    def _build(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        page = QWidget()
        page.setObjectName('page')
        scroll.setWidget(page)
        self.setCentralWidget(scroll)
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 16, 24, 22)
        root.setSpacing(16)

        # 漂浮導覽列：等寬字標誌＋標題，右側狀態徽章
        nav = QFrame()
        nav.setObjectName('nav')
        header = QHBoxLayout(nav)
        header.setContentsMargins(22, 10, 14, 10)
        header.setSpacing(12)
        mark = QLabel('digit_arm')
        mark.setObjectName('wordmark')
        sep = QLabel('/')
        sep.setObjectName('navSep')
        t = QLabel('Air Digits')
        t.setObjectName('navTitle')
        t.setToolTip('Write a number in the air with your index finger. The arm moves that many times.')
        header.addWidget(mark)
        header.addWidget(sep)
        header.addWidget(t)
        header.addStretch(1)
        pills = QHBoxLayout()
        pills.setSpacing(8)
        self.pill_mode = Pill()
        self.pill_cam = Pill()
        self.pill_arm = Pill()
        for pl in (self.pill_mode, self.pill_cam, self.pill_arm):
            pl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            pills.addWidget(pl, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addLayout(pills)
        root.addWidget(nav)
        _float_shadow(nav)
        self.header_pills = pills

        # 主體
        self.body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.body.setSpacing(16)
        root.addLayout(self.body, 1)

        # 左：鏡頭
        self.cam_card = Card()
        self.cam_card.body.setContentsMargins(16, 16, 16, 16)
        self.cam_card.body.setSpacing(12)
        self.video = VideoView()
        self.cam_card.body.addWidget(self.video, 1)
        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        self.hints = HintRow()
        bottom.addWidget(self.hints)
        ink_box = QVBoxLayout()
        ink_box.setSpacing(4)
        ink_title = QLabel('Your writing · each box is one digit, leave space between digits')
        ink_title.setObjectName('monoCaption')
        ink_title.setWordWrap(True)
        self.ink_view = InkView()
        ink_box.addWidget(ink_title)
        ink_box.addWidget(self.ink_view, 1)
        bottom.addLayout(ink_box, 1)
        self.cam_card.body.addLayout(bottom)
        self.body.addWidget(self.cam_card, 1)

        # 右：狀態、結果、進度、按鈕、筆跡
        self.side = QWidget()
        side = QVBoxLayout(self.side)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(12)

        self.result_card = Card('STATUS')
        self.headline = QLabel()
        self.headline.setObjectName('headline')
        self.headline.setWordWrap(True)
        self.detail = QLabel()
        self.detail.setObjectName('detail')
        self.detail.setWordWrap(True)
        self.big = QLabel()
        self.big.setObjectName('bigNumber')
        self.big.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.conf = QLabel()
        self.conf.setObjectName('caption')
        self.conf.setWordWrap(True)
        self.result_card.body.addWidget(self.headline)
        self.result_card.body.addWidget(self.big)
        self.result_card.body.addWidget(self.detail)
        self.result_card.body.addWidget(self.conf)
        side.addWidget(self.result_card)

        self.progress_card = Card('PROGRESS')
        row = QHBoxLayout()
        self.progress_text = QLabel('0 / 0')
        self.progress_text.setObjectName('progressText')
        self.progress_phase = QLabel('Not started')
        self.progress_phase.setObjectName('detail')
        self.progress_phase.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.progress_phase.setWordWrap(True)
        row.addWidget(self.progress_text)
        row.addWidget(self.progress_phase, 1)
        self.progress_card.body.addLayout(row)
        self.progress_bar = RoundedProgress()
        self.progress_card.body.addWidget(self.progress_bar)
        self.sim_banner = QLabel()
        self.sim_banner.setObjectName('simBanner')
        self.sim_banner.setWordWrap(True)
        self.progress_card.body.addWidget(self.sim_banner)
        self.arm_detail = QLabel()
        self.arm_detail.setObjectName('caption')
        self.arm_detail.setWordWrap(True)
        self.progress_card.body.addWidget(self.arm_detail)
        side.addWidget(self.progress_card)

        self.btn_start = QPushButton('Start')
        self.btn_start.setObjectName('primary')
        self.btn_start.clicked.connect(self._on_start)
        side.addWidget(self.btn_start)
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.btn_clear = QPushButton('Clear')
        self.btn_clear.setObjectName('gray')
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_cancel = QPushButton('Cancel')
        self.btn_cancel.setObjectName('danger')
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_cancel.setToolTip('You can also press Esc')
        row2.addWidget(self.btn_clear, 1)
        row2.addWidget(self.btn_cancel, 1)
        side.addLayout(row2)
        row3 = QHBoxLayout()
        self.btn_connect = QPushButton('Connect arm')
        self.btn_connect.setObjectName('connect')
        self.btn_connect.clicked.connect(self._on_connect)
        self.btn_camera = QPushButton('Turn off camera')
        self.btn_camera.setObjectName('plain')
        self.btn_camera.clicked.connect(self._on_camera)
        row3.addWidget(self.btn_connect, 1)
        row3.addWidget(self.btn_camera, 1)
        side.addLayout(row3)

        side.addStretch(1)
        self.body.addWidget(self.side)
        # 鏡頭卡片每秒重繪 30 次，不加陰影效果以免拖慢預覽
        for card in (self.result_card, self.progress_card):
            _float_shadow(card)
        for b in (self.btn_start, self.btn_clear, self.btn_cancel, self.btn_connect, self.btn_camera):
            b.setCursor(Qt.CursorShape.PointingHandCursor)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        narrow = self.width() < NARROW_WIDTH
        if narrow == self._narrow:
            return
        self._narrow = narrow
        if narrow:
            self.body.setDirection(QBoxLayout.Direction.TopToBottom)
            self.side.setMinimumWidth(0)
            self.side.setMaximumWidth(16777215)
            self.video.setMinimumHeight(300)
            self.side.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        else:
            self.body.setDirection(QBoxLayout.Direction.LeftToRight)
            self.side.setFixedWidth(SIDE_WIDTH)
            self.video.setMinimumHeight(260)
            self.side.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.body.setStretch(0, 1)
        self.body.setStretch(1, 0)

    # ── Bridge ──────────────────────────────────────────────
    def _connect_bridge(self):
        b = self.bridge
        b.frame.connect(self._on_frame)
        b.hand.connect(self._on_hand)
        b.ink.connect(self._on_ink)
        b.recognition.connect(self._on_recognition)
        b.camera_status.connect(self._on_camera_status)
        b.arm_status.connect(self._on_arm)
        b.executor_online.connect(self._on_online)
        b.motion_accepted.connect(self._on_accepted)
        b.motion_rejected.connect(self._on_rejected)
        b.motion_feedback.connect(self._on_feedback)
        b.motion_done.connect(self._on_done)
        b.service_result.connect(self._on_service)

    def _on_frame(self):
        img = self.bridge.take_frame()
        if img is not None:
            self.video.set_image(img)

    def _on_hand(self, d):
        self.st.hand = d
        self.video.hand = d
        self.hints.set_active({'point': 'point', 'open': 'open', 'fist': 'fist'}.get(d.get('gesture'), ''))
        self.video.update()
        self._refresh_headline()

    def _on_ink(self, d):
        new_ink = d.get('ink_id') != self.st.ink.get('ink_id')
        self.st.ink = d
        if new_ink:
            # 新的一次書寫：清掉上一輪的結果與進度
            self.st.notice = ''
            if not self.st.running:
                self.st.finished = None
                self.st.progress = {'completed': 0, 'total': 0, 'phase': ''}
        self.video.strokes = d.get('strokes', [])
        self.video.pen_down = d.get('pen_down', False)
        self.video.update()
        self._refresh()

    def _on_recognition(self, d):
        self.st.recognition = d
        self._refresh()

    def _on_camera_status(self, d):
        self.st.camera = d
        self.st.camera_pending = False
        state = d.get('state')
        if state in ('off', 'error'):
            self.video.hand = {}
            self.st.hand = {}
            self.hints.set_active('')
            self.video.set_image(None)
        self.video.placeholder = {'off': 'Camera is off', 'starting': 'Opening camera...'}.get(
            state, d.get('message') or 'Camera not available')
        if state == 'error':
            self.video.placeholder = 'Camera not available\n' + (d.get('message') or '')
        self.video.placeholder_tone = 'error' if state == 'error' else 'neutral'
        self.video.source_label = d.get('source') if 'Synthetic' in (d.get('source') or '') else ''
        self._refresh()

    def _on_arm(self, d):
        self.st.arm = d
        self._refresh()

    def _on_online(self, online):
        self.st.executor_online = online
        if not online and self.st.running:
            self.st.running = False
            self.st.cancelling = False
            self.st.finished = {'outcome': 'aborted', 'message': 'Lost the motion node, so the arm state is unknown',
                                'ink_id': self.st.ink.get('ink_id')}
        self._refresh()

    def _on_accepted(self):
        self.st.goal_pending = False
        self.st.running = True
        self.st.cancelling = False
        self._refresh()

    def _on_rejected(self, reason):
        self.st.goal_pending = False
        arm_msg = (self.st.arm or {}).get('message', '')
        self.st.notice = f'{reason}: {arm_msg}' if arm_msg else reason
        self._refresh()

    def _on_feedback(self, d):
        self.st.progress = d
        self._refresh()

    def _on_done(self, d):
        self.st.running = False
        self.st.cancelling = False
        self.st.goal_pending = False
        d = dict(d)
        d['ink_id'] = self.st.ink.get('ink_id')
        self.st.finished = d
        self.st.progress = {'completed': d.get('completed', 0), 'total': d.get('requested', 0), 'phase': ''}
        self._refresh()

    def _on_service(self, name, ok, message):
        if name == 'camera':
            self.st.camera_pending = False
        if not ok:
            self.st.notice = message
        elif name == 'clear':
            self.st.finished = None
            self.st.notice = ''
            self.st.progress = {'completed': 0, 'total': 0, 'phase': ''}
        self._refresh()

    # ── 使用者操作 ──────────────────────────────────────────
    def _on_start(self):
        rec = self.st.current_recognition()
        if not self.btn_start.isEnabled() or not rec or not rec.get('success'):
            return
        self.st.goal_pending = True
        self.st.notice = ''
        self.st.progress = {'completed': 0, 'total': int(rec['value']), 'phase': ''}
        self._refresh()
        self.bridge.start_motion(int(rec['value']), int(rec['ink_id']))

    def _on_cancel(self):
        if self.st.phase() != RUNNING:
            return
        self.st.cancelling = True
        self._refresh()
        self.bridge.cancel_motion()

    def _on_clear(self):
        if not self.btn_clear.isEnabled():
            return
        self.bridge.clear_ink()

    def _on_camera(self):
        on = self.st.camera.get('state') not in ('starting', 'streaming')
        self.st.camera_pending = True
        self._refresh()
        self.bridge.set_camera(on)

    def _on_connect(self):
        ret = QMessageBox.question(
            self, 'Connect the arm',
            'Connecting restarts the Arduino. The pose of the arm right now becomes the start pose.\n\n'
            'Before you connect:\n• Put the arm in the movearm HOME pose\n• Keep people and things away\n'
            '• Close other apps that use the serial port\n\nConnect now?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if ret == QMessageBox.StandardButton.Yes:
            self.st.notice = ''
            self.bridge.connect_arm()

    # ── 畫面更新 ────────────────────────────────────────────
    def _refresh_headline(self):
        h = self.st.headline()
        self.headline.setText(h.title)
        self.headline.setStyleSheet(f'color: {S.TONE[h.tone] if h.tone != "neutral" else S.STRONG};')
        self.detail.setText(self.st.notice or h.subtitle)
        self.detail.setStyleSheet(f'color: {S.RED};' if self.st.notice else '')

    def _refresh(self):
        st = self.st
        ph = st.phase()
        self._refresh_headline()

        rec = st.current_recognition()
        if rec and rec.get('success') and ph in (RESULT_OK, RUNNING, CANCELLING) or \
                (rec and rec.get('success') and st.finished):
            self.big.setText(str(rec['value']))
            self.big.setStyleSheet(f'color: {S.STRONG};')
            self.big.show()
            self.headline.setText('You wrote' if ph == RESULT_OK else self.headline.text())
            digits = rec.get('digits') or []
            parts = [f'{d["label"]}: {d["confidence"] * 100:.0f}%' for d in digits]
            self.conf.setText('Confidence   ' + '   '.join(parts) if parts else '')
            self.conf.show()
        elif ph == RESULT_FAIL:
            self.big.setText('?')
            self.big.setStyleSheet(f'color: {S.RED};')
            self.big.show()
            self.conf.setText('The arm never moves when the reading is unsure')
            self.conf.show()
        else:
            self.big.hide()
            self.conf.hide()

        # 進度
        p = st.progress
        total = int(p.get('total') or 0)
        done = int(p.get('completed') or 0)
        self.progress_text.setText(f'{done} / {total}' if total else '— / —')
        color = S.YELLOW if ph == CANCELLING else (S.TEAL if st.finished and st.finished.get('outcome') == 'succeeded'
                                                   else S.LABEL)
        self.progress_bar.set_value(done / total if total else 0.0, color)
        if ph in (RUNNING, CANCELLING):
            self.progress_phase.setText(st.headline().subtitle)
        elif st.finished:
            self.progress_phase.setText({'succeeded': 'Done', 'canceled': 'Canceled'}.get(
                st.finished.get('outcome'), 'Stopped'))
        else:
            self.progress_phase.setText('Not started')

        arm = st.arm or {}
        if not st.executor_online or not arm:
            self.sim_banner.setText('Motion node not connected yet')
            self.sim_banner.show()
        elif arm.get('simulated'):
            self.sim_banner.setText('Simulation: the arm does not really move. Progress is only a timer.')
            self.sim_banner.show()
        else:
            self.sim_banner.hide()
        info = []
        if arm.get('motion_description'):
            info.append('Each move: ' + arm['motion_description'])
        if arm.get('max_count'):
            info.append(f'Max {arm["max_count"]} times')
        if arm.get('mode') == 'hardware' and arm.get('message'):
            info.append(arm['message'])
        self.arm_detail.setText('   '.join(info[:2]) + ('\n' + info[2] if len(info) > 2 else ''))

        # 膠囊
        if arm.get('mode') == 'hardware':
            self.pill_mode.set_tone('active', 'Real arm')
        elif arm:
            self.pill_mode.set_tone('warn', 'Simulation')
        else:
            self.pill_mode.set_tone('neutral', 'No motion node')
        cam = st.camera.get('state')
        fps = st.camera.get('fps') or 0
        self.pill_cam.set_tone({'streaming': 'ok', 'starting': 'active', 'error': 'error'}.get(cam, 'neutral'),
                               {'streaming': f'Camera {fps:.0f} fps' if fps else 'Camera on', 'starting': 'Camera starting',
                                'error': 'Camera error'}.get(cam, 'Camera off'))
        if arm.get('mode') == 'hardware':
            if arm.get('connected') and arm.get('position_trusted'):
                self.pill_arm.set_tone('ok', 'Connected')
            elif arm.get('connected'):
                self.pill_arm.set_tone('error', 'Position unknown')
            else:
                self.pill_arm.set_tone('warn', 'Not connected')
            self.pill_arm.show()
        else:
            self.pill_arm.hide()

        # 按鈕
        b = st.buttons()
        self.btn_start.setEnabled(b.start)
        self.btn_start.setText(b.start_text + '  →')
        self.btn_cancel.setEnabled(b.cancel)
        self.btn_cancel.setText('Canceling...' if ph == CANCELLING else 'Cancel')
        self.btn_clear.setEnabled(b.clear)
        self.btn_camera.setEnabled(b.camera)
        self.btn_camera.setText(b.camera_text)
        self.btn_connect.setVisible(b.connect_visible)
        self.btn_connect.setEnabled(b.connect)

        # 筆跡卡
        digits = rec.get('digits') if rec else []
        self.ink_view.set_data(st.ink.get('strokes', []), digits or [])

    def closeEvent(self, e):
        if self.st.running:
            self.bridge.cancel_motion()
        super().closeEvent(e)


def create_app(argv):
    os.environ.setdefault('QT_AUTO_SCREEN_SCALE_FACTOR', '1')
    app = QApplication.instance() or QApplication(argv)
    app.setStyle('Fusion')
    app.setFont(S.font(14))
    app.setStyleSheet(S.STYLESHEET)
    return app


def main(args=None):
    from .ros_bridge import RosBridge
    app = create_app(sys.argv)
    bridge = RosBridge(args=args)
    win = MainWindow(bridge)
    win.show()
    # Ctrl+C 或 launch 結束時關閉視窗；計時器讓 Python 有機會處理訊號
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    tick = QTimer()
    tick.start(200)
    tick.timeout.connect(lambda: None)
    try:
        code = app.exec()
    finally:
        bridge.shutdown()
    return code


if __name__ == '__main__':
    sys.exit(main())
