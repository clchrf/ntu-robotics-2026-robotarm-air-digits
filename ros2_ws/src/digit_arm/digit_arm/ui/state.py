"""介面狀態：由各節點的最新訊息推導目前階段、按鈕啟用與提示文字。不依賴 Qt，可單獨測試。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

WRITING = 'writing'
RECOGNIZING = 'recognizing'
RESULT_OK = 'result_ok'
RESULT_FAIL = 'result_fail'
STARTING = 'starting'
RUNNING = 'running'
CANCELLING = 'cancelling'
FINISHED = 'finished'

PHASE_LABEL = {'out': 'Moving out', 'dwell': 'Holding', 'return': 'Going back', 'pause': 'Next one',
               'cancel_return': 'Canceling: going back'}


@dataclass
class Headline:
    title: str
    subtitle: str
    tone: str  # neutral / active / ok / warn / error


@dataclass
class Buttons:
    start: bool
    start_text: str
    cancel: bool
    clear: bool
    camera: bool
    camera_text: str
    connect_visible: bool
    connect: bool


@dataclass
class UiState:
    camera: dict = field(default_factory=lambda: {'state': 'off', 'message': 'Waiting for the camera...', 'source': '', 'fps': 0.0})
    hand: dict = field(default_factory=dict)
    ink: dict = field(default_factory=lambda: {'ink_id': 0, 'locked': False, 'pen_down': False, 'strokes': []})
    recognition: Optional[dict] = None
    arm: Optional[dict] = None
    executor_online: bool = False
    goal_pending: bool = False
    running: bool = False
    cancelling: bool = False
    progress: dict = field(default_factory=lambda: {'completed': 0, 'total': 0, 'phase': ''})
    finished: Optional[dict] = None     # 動作結果（附 ink_id）
    camera_pending: bool = False
    notice: str = ''                    # 一次性的提示（例如被拒絕的原因）

    # ── 推導 ──
    def current_recognition(self) -> Optional[dict]:
        rec = self.recognition
        if rec and self.ink.get('locked') and rec.get('ink_id') == self.ink.get('ink_id'):
            return rec
        return None

    def phase(self) -> str:
        if self.running:
            return CANCELLING if self.cancelling else RUNNING
        if self.goal_pending:
            return STARTING
        if self.finished and self.finished.get('ink_id') == self.ink.get('ink_id'):
            return FINISHED
        if self.ink.get('locked'):
            rec = self.current_recognition()
            if rec is None:
                return RECOGNIZING
            return RESULT_OK if rec.get('success') else RESULT_FAIL
        return WRITING

    def has_ink(self) -> bool:
        return any(len(s) > 1 for s in self.ink.get('strokes', []))

    def simulated(self) -> bool:
        return bool(self.arm and self.arm.get('simulated'))

    def buttons(self) -> Buttons:
        ph = self.phase()
        arm = self.arm or {}
        rec = self.current_recognition()
        can_start = (ph == RESULT_OK and self.executor_online and bool(arm.get('ready')))
        value = int(rec['value']) if rec and rec.get('success') else 0
        start_text = f'Start ({value} {"time" if value == 1 else "times"})' if ph == RESULT_OK and value else 'Start'
        cam_on = self.camera.get('state') in ('starting', 'streaming')
        return Buttons(
            start=can_start,
            start_text=start_text,
            cancel=ph == RUNNING,
            clear=ph not in (RUNNING, CANCELLING, STARTING) and (self.has_ink() or bool(self.ink.get('locked'))),
            camera=not self.camera_pending,
            camera_text='Turn off camera' if cam_on else 'Turn on camera',
            connect_visible=arm.get('mode') == 'hardware',
            connect=arm.get('mode') == 'hardware' and not arm.get('connected') and ph not in (RUNNING, CANCELLING)
            and not self.goal_pending,
        )

    def headline(self) -> Headline:
        ph = self.phase()
        cam = self.camera.get('state')
        if ph in (RUNNING, CANCELLING):
            p = self.progress
            sim = ' (simulation)' if self.simulated() else ''
            step = PHASE_LABEL.get(p.get('phase', ''), 'Moving')
            if ph == CANCELLING:
                return Headline('Canceling...', 'No new moves. The arm goes back to start, then stops.' + sim, 'warn')
            return Headline(f'Moving {p.get("completed", 0)} / {p.get("total", 0)}', f'{step}{sim}', 'active')
        if ph == STARTING:
            return Headline('Sending...', 'Waiting for the arm', 'active')
        if ph == FINISHED:
            f = self.finished or {}
            tone = {'succeeded': 'ok', 'canceled': 'warn'}.get(f.get('outcome'), 'error')
            title = {'succeeded': 'Done', 'canceled': 'Canceled'}.get(f.get('outcome'), 'Stopped')
            return Headline(title, (f.get('message') or '') + '. Press "Clear" to write again.', tone)
        if ph == RESULT_OK:
            rec = self.current_recognition() or {}
            v = rec.get('value')
            times = 'time' if v == 1 else 'times'
            return Headline(f'You wrote {v}', f'If this is right, press "Start". The arm will move {v} {times}.', 'ok')
        if ph == RESULT_FAIL:
            rec = self.current_recognition() or {}
            return Headline('Could not read your number', rec.get('reason') or 'Please clear and rewrite.', 'error')
        if ph == RECOGNIZING:
            return Headline('Reading...', 'Checking what you wrote', 'active')
        if cam == 'error':
            return Headline('Camera not available', self.camera.get('message') or 'Make sure no other app uses the camera.', 'error')
        if cam in ('off', None):
            return Headline('Camera is off', self.camera.get('message') or 'Press "Turn on camera" to start.', 'neutral')
        if cam == 'starting':
            return Headline('Opening camera...', self.camera.get('message') or '', 'neutral')
        hand = self.hand or {}
        if not hand.get('hand_visible'):
            msg = hand.get('message') or 'Show your hand to the camera'
            tone = 'warn' if ('Lost' in msg or 'left the camera' in msg) else 'neutral'
            return Headline('Cannot see your hand' if tone == 'warn' else 'Show your hand', msg, tone)
        if hand.get('pen') == 'down':
            return Headline('Writing...', 'Open your hand for the next stroke. Make a fist when done.', 'active')
        if hand.get('gesture') == 'fist' and hand.get('fist_progress', 0) > 0:
            return Headline('Hold the fist...', f'{int(hand.get("fist_progress", 0) * 100)}%. Keep still to send.', 'active')
        return Headline(hand.get('message') or 'Point your index finger to write',
                        'Leave space between digits. Make a fist when done.', 'neutral')
