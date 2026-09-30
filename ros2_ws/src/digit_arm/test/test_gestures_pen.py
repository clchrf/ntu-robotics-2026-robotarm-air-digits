"""手勢判斷與下筆／抬筆／握拳狀態機。"""

import numpy as np
import pytest

from digit_arm.core import gestures
from digit_arm.core.pen import HandObservation, PenConfig, PenTracker
from digit_arm.core.synthetic_hand import hand_landmarks

FPS = 30.0


@pytest.mark.parametrize('g', [gestures.POINT, gestures.OPEN, gestures.FIST, gestures.OTHER])
@pytest.mark.parametrize('roll', [-45, -20, 0, 20, 45])
def test_classify_rotation_invariant(g, roll):
    rng = np.random.default_rng(roll + 100)
    lm = hand_landmarks(g, (0.5, 0.5), roll_deg=roll, rng=rng, noise=0.004)
    assert gestures.classify(gestures.to_metric(lm, 640, 480)) == g


def test_classify_without_hand():
    assert gestures.classify(None) == gestures.NONE
    assert gestures.classify([]) == gestures.NONE


class Driver:
    def __init__(self, cfg=None):
        self.pen = PenTracker(cfg or PenConfig())
        self.t = 0.0
        self.events = []

    def feed(self, gesture, xy=(0.5, 0.5), n=1, dx=0.0, dy=0.0):
        snap = None
        for i in range(n):
            obs = None if gesture is None else HandObservation(gesture, (xy[0] + dx * i, xy[1] + dy * i))
            snap = self.pen.update(self.t, obs)
            self.events += snap.events
            self.t += 1 / FPS
        return snap


def test_point_draws_stroke_and_open_palm_lifts_pen():
    d = Driver()
    d.feed(gestures.POINT, (0.3, 0.3), n=30, dy=0.01)
    assert d.pen.pen_down
    d.feed(gestures.OPEN, (0.5, 0.3), n=10, dx=0.01)
    assert not d.pen.pen_down
    assert len(d.pen.strokes) == 1
    # 抬筆移動期間不留下點
    ys = [p[1] for p in d.pen.strokes[0]]
    assert max(ys) <= 0.3 + 0.3
    assert d.events.count('stroke_started') == 1 and d.events.count('stroke_ended') == 1


def test_single_frame_glitches_do_not_start_or_break_strokes():
    d = Driver()
    for _ in range(5):
        d.feed(gestures.OPEN, n=4)
        d.feed(gestures.POINT, n=1)          # 瞬間誤判不下筆
    assert d.pen.strokes == [] and not d.pen.pen_down
    d.feed(gestures.POINT, (0.2, 0.2), n=20, dx=0.01)
    d.feed(gestures.OTHER, n=1)              # 瞬間誤判不斷筆
    d.feed(gestures.POINT, (0.4, 0.2), n=20, dx=0.01)
    d.feed(gestures.OPEN, n=6)
    assert len(d.pen.strokes) == 1


def test_short_fist_does_not_submit_but_held_fist_does():
    d = Driver(PenConfig(fist_hold_s=0.8))
    d.feed(gestures.POINT, (0.3, 0.3), n=30, dy=0.01)
    d.feed(gestures.OPEN, n=5)
    d.feed(gestures.FIST, n=10)              # 0.33 秒
    d.feed(gestures.OPEN, n=5)
    assert not d.pen.locked and 'submitted' not in d.events
    d.feed(gestures.FIST, n=12)
    d.feed(gestures.OTHER, n=1)              # 維持期間一格誤判可容忍
    d.feed(gestures.FIST, n=16)
    assert d.pen.locked and d.events.count('submitted') == 1


def test_fist_without_ink_reports_empty():
    d = Driver()
    d.feed(gestures.FIST, n=40)
    assert 'empty_submit' in d.events and not d.pen.locked


def test_locked_ignores_writing_until_clear():
    d = Driver()
    d.feed(gestures.POINT, (0.3, 0.3), n=30, dy=0.01)
    d.feed(gestures.FIST, n=40)
    assert d.pen.locked
    ink_id = d.pen.ink_id
    d.feed(gestures.OPEN, n=3)
    d.feed(gestures.POINT, (0.6, 0.3), n=30, dy=0.01)
    assert len(d.pen.strokes) == 1
    d.pen.clear()
    assert d.pen.ink_id == ink_id + 1 and not d.pen.locked and d.pen.strokes == []
    d.feed(gestures.POINT, (0.6, 0.3), n=30, dy=0.01)
    assert d.pen.pen_down


def test_fist_still_held_after_clear_does_not_resubmit():
    d = Driver()
    d.feed(gestures.POINT, (0.3, 0.3), n=30, dy=0.01)
    d.feed(gestures.FIST, n=40)
    d.pen.clear()
    d.feed(gestures.FIST, n=60)
    assert not d.pen.locked


def test_tracking_loss_ends_stroke_and_resets_fist():
    d = Driver(PenConfig(lost_timeout_s=0.3))
    d.feed(gestures.POINT, (0.3, 0.3), n=30, dy=0.01)
    d.feed(None, n=3)                        # 0.1 秒：短暫遺失，維持
    assert d.pen.pen_down
    snap = d.feed(None, n=12)
    assert not d.pen.pen_down and len(d.pen.strokes) == 1
    assert 'tracking_lost' in d.events and 'left the camera' in snap.message
    assert d.pen.interrupted, '書寫中斷線的筆跡要標記為不完整'
    snap = d.feed(gestures.OPEN, n=1)
    assert 'tracking_restored' in snap.events
    d.pen.clear()
    assert not d.pen.interrupted


def test_tracking_loss_while_pen_up_is_not_interruption():
    d = Driver(PenConfig(lost_timeout_s=0.3))
    d.feed(gestures.POINT, (0.3, 0.3), n=30, dy=0.01)
    d.feed(gestures.OPEN, n=6)
    d.feed(None, n=20)           # 抬筆後手離開畫面：筆畫已完整，不算中斷
    assert len(d.pen.strokes) == 1 and not d.pen.interrupted


def test_trim_removes_tail_when_finger_curls():
    d = Driver(PenConfig(trim_tail_s=0.1))
    d.feed(gestures.POINT, (0.3, 0.3), n=30, dy=0.01)
    d.feed(gestures.POINT, (0.3, 0.6), n=4, dx=0.03)   # 手指彎下去前的橫向位移
    d.feed(gestures.OPEN, n=5)
    xs = [p[0] for p in d.pen.strokes[0]]
    assert max(xs) < 0.36
