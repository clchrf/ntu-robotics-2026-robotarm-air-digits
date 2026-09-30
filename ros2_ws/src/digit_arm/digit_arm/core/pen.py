"""把逐格手勢轉成筆畫：下筆、抬筆、握拳送出。

設計重點
- 下筆與抬筆都要連續數格一致才切換（去抖動），避免一筆被瞬間誤判切斷。
- 從食指切換到其他手勢時，手指彎下去的那一小段會被剪掉（trim_tail_s），不留尾巴。
- 握拳必須在 fist_hold_s 內穩定維持（容許少量誤判格）才送出。
- 手部追蹤中斷超過 lost_timeout_s：結束目前筆畫、重置握拳計時。
- 送出後筆跡鎖定，直到呼叫 clear()。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import gestures


@dataclass
class PenConfig:
    down_frames: int = 3
    up_frames: int = 3
    fist_hold_s: float = 0.8
    fist_min_ratio: float = 0.8
    fist_break_frames: int = 3
    lost_timeout_s: float = 0.35
    min_point_dist: float = 0.003
    trim_tail_s: float = 0.10
    min_stroke_length: float = 0.015
    max_strokes: int = 40
    max_points: int = 6000
    # One Euro 濾波（指尖平滑）
    filter_min_cutoff: float = 1.5
    filter_beta: float = 8.0
    filter_d_cutoff: float = 1.0


class OneEuroFilter:
    """低延遲平滑：慢速時濾得多、快速時跟得緊。"""

    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x = None
        self._dx = 0.0
        self._t = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self):
        self._x = None
        self._dx = 0.0
        self._t = None

    def __call__(self, t: float, x: float) -> float:
        if self._x is None or self._t is None or t <= self._t:
            self._x, self._t, self._dx = x, t, 0.0
            return x
        dt = t - self._t
        dx = (x - self._x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self._dx = a_d * dx + (1 - a_d) * self._dx
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        a = self._alpha(cutoff, dt)
        self._x = a * x + (1 - a) * self._x
        self._t = t
        return self._x


@dataclass
class HandObservation:
    gesture: str
    tip: Tuple[float, float]


@dataclass
class PenSnapshot:
    pen: str                 # down / up / locked
    gesture: str
    hand_visible: bool
    fist_progress: float
    tip: Optional[Tuple[float, float]]
    message: str
    events: List[str] = field(default_factory=list)


TimedPoint = Tuple[float, float, float]  # x, y, t


def stroke_length(points) -> float:
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += math.hypot(b[0] - a[0], b[1] - a[1])
    return total


class PenTracker:
    def __init__(self, cfg: PenConfig | None = None):
        self.cfg = cfg or PenConfig()
        self._fx = OneEuroFilter(self.cfg.filter_min_cutoff, self.cfg.filter_beta, self.cfg.filter_d_cutoff)
        self._fy = OneEuroFilter(self.cfg.filter_min_cutoff, self.cfg.filter_beta, self.cfg.filter_d_cutoff)
        self.ink_id = 1
        self.strokes: List[List[TimedPoint]] = []
        self.current: Optional[List[TimedPoint]] = None
        self.locked = False
        self.revision = 0
        # 有筆畫因為手離開畫面（追蹤中斷）而被迫結束：筆跡可能不完整，辨識時必須拒絕
        self.interrupted = False
        self._pending_down: List[TimedPoint] = []
        self._point_run = 0
        self._nonpoint_run = 0
        self._fist_since: Optional[float] = None
        self._fist_frames: List[bool] = []
        self._fist_break = 0
        self._fist_armed = True
        self._last_seen: Optional[float] = None
        self._lost_reported = False
        self._last_tip: Optional[Tuple[float, float]] = None

    # ── 公開操作 ──────────────────────────────────────────────
    def clear(self):
        self.strokes = []
        self.current = None
        self.locked = False
        self.interrupted = False
        self._pending_down = []
        self._point_run = 0
        self._nonpoint_run = 0
        self._reset_fist()
        self._fist_armed = False  # 手還是握拳時不要立刻再次送出
        self.ink_id += 1
        self.revision += 1

    def finish_stroke(self):
        """外部要求結束目前筆畫（例如關閉鏡頭）。"""
        if self.current is not None:
            self._end_stroke(trim=False)

    @property
    def pen_down(self) -> bool:
        return self.current is not None

    def all_strokes(self) -> List[List[TimedPoint]]:
        if self.current is not None and len(self.current) >= 1:
            return self.strokes + [self.current]
        return list(self.strokes)

    def has_ink(self) -> bool:
        return bool(self.strokes) or (self.current is not None and len(self.current) > 1)

    # ── 逐格更新 ──────────────────────────────────────────────
    def update(self, t: float, obs: Optional[HandObservation]) -> PenSnapshot:
        events: List[str] = []
        if obs is None or obs.gesture == gestures.NONE:
            return self._update_lost(t, events)

        if self._lost_reported:
            events.append('tracking_restored')
        self._lost_reported = False
        self._last_seen = t
        x = self._fx(t, obs.tip[0])
        y = self._fy(t, obs.tip[1])
        self._last_tip = (x, y)

        if self.locked:
            self._track_fist_release(obs.gesture)
            return self._snapshot(obs.gesture, True, events, 'Sent. Press "Clear" to write again.')

        is_point = obs.gesture == gestures.POINT
        is_fist = obs.gesture == gestures.FIST

        # 下筆／抬筆（去抖動）
        if is_point:
            self._point_run += 1
            self._nonpoint_run = 0
            if self.current is None:
                self._pending_down.append((x, y, t))
                if self._point_run >= self.cfg.down_frames:
                    self._start_stroke()
                    events.append('stroke_started')
            else:
                self._add_point(x, y, t)
        else:
            self._nonpoint_run += 1
            self._point_run = 0
            self._pending_down = []
            if self.current is not None and self._nonpoint_run >= self.cfg.up_frames:
                self._end_stroke(trim=True)
                events.append('stroke_ended')

        # 握拳送出
        submitted = self._update_fist(t, is_fist)
        if submitted:
            if self.current is not None:
                self._end_stroke(trim=True)
                events.append('stroke_ended')
            if self.has_ink():
                self.locked = True
                self.revision += 1
                events.append('submitted')
            else:
                events.append('empty_submit')

        msg = self._message(obs.gesture)
        if 'empty_submit' in events:
            msg = 'Nothing written yet. Point your index finger to write.'
        return self._snapshot(obs.gesture, True, events, msg)

    # ── 內部 ──────────────────────────────────────────────────
    def _update_lost(self, t: float, events: List[str]) -> PenSnapshot:
        self._point_run = 0
        self._pending_down = []
        if self._last_seen is None:
            return self._snapshot(gestures.NONE, False, events, 'Show your hand to the camera')
        if t - self._last_seen >= self.cfg.lost_timeout_s:
            if not self._lost_reported:
                self._lost_reported = True
                events.append('tracking_lost')
                self._reset_fist()
                self._fx.reset()
                self._fy.reset()
                if self.current is not None:
                    self._end_stroke(trim=False)
                    self.interrupted = True
                    events.append('stroke_ended')
                    events.append('stroke_interrupted')
            if self.interrupted:
                return self._snapshot(gestures.NONE, False, events,
                                      'Hand left the camera, so the stroke broke. Press "Clear" and write inside the box.')
            return self._snapshot(gestures.NONE, False, events, 'Lost your hand. Put it back in view.')
        # 短暫遺失：維持狀態
        return self._snapshot(gestures.NONE, False, events, 'Tracking...')

    def _start_stroke(self):
        if len(self.strokes) >= self.cfg.max_strokes:
            self._pending_down = []
            return
        pts = []
        for p in self._pending_down:
            if not pts or math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) >= self.cfg.min_point_dist:
                pts.append(p)
        self.current = pts
        self._pending_down = []
        self.revision += 1

    def _add_point(self, x: float, y: float, t: float):
        cur = self.current
        if cur is None:
            return
        if sum(len(s) for s in self.strokes) + len(cur) >= self.cfg.max_points:
            return
        if not cur or math.hypot(x - cur[-1][0], y - cur[-1][1]) >= self.cfg.min_point_dist:
            cur.append((x, y, t))
            self.revision += 1

    def _end_stroke(self, trim: bool):
        pts = self.current or []
        self.current = None
        if trim and pts:
            # 剪掉切換手勢前那段（手指開始彎曲時的位移）
            cutoff = pts[-1][2] - self.cfg.trim_tail_s
            kept = [p for p in pts if p[2] <= cutoff]
            if len(kept) >= 2:
                pts = kept
        if len(pts) >= 2 and stroke_length(pts) >= self.cfg.min_stroke_length:
            self.strokes.append(pts)
        self.revision += 1

    def _reset_fist(self):
        self._fist_since = None
        self._fist_frames = []
        self._fist_break = 0

    def _track_fist_release(self, gesture: str):
        if gesture != gestures.FIST:
            self._fist_armed = True
        self._reset_fist()

    def _update_fist(self, t: float, is_fist: bool) -> bool:
        if not is_fist:
            self._fist_armed = True
        if not self._fist_armed:
            return False
        if self._fist_since is None:
            if is_fist:
                self._fist_since = t
                self._fist_frames = [True]
                self._fist_break = 0
            return False
        self._fist_frames.append(is_fist)
        self._fist_break = 0 if is_fist else self._fist_break + 1
        if self._fist_break >= self.cfg.fist_break_frames:
            self._reset_fist()
            return False
        if t - self._fist_since >= self.cfg.fist_hold_s:
            ratio = sum(self._fist_frames) / len(self._fist_frames)
            self._reset_fist()
            if ratio >= self.cfg.fist_min_ratio:
                self._fist_armed = False
                return True
        return False

    def fist_progress(self, t: Optional[float] = None) -> float:
        if self._fist_since is None or not self._fist_frames:
            return 0.0
        now = t if t is not None else (self._last_seen or self._fist_since)
        return max(0.0, min(1.0, (now - self._fist_since) / self.cfg.fist_hold_s))

    def _message(self, gesture: str) -> str:
        if gesture == gestures.POINT:
            return 'Writing with your index finger' if self.current is not None else 'Getting ready to write...'
        if gesture == gestures.FIST:
            return 'Hold the fist...' if self.has_ink() else 'Nothing written yet. Point your index finger to write.'
        if gesture == gestures.OPEN:
            return 'Pen up: move to the next stroke or digit'
        return 'Pen up (hand shape unclear)'

    def _snapshot(self, gesture: str, visible: bool, events: List[str], message: str) -> PenSnapshot:
        pen = 'locked' if self.locked else ('down' if self.current is not None else 'up')
        prog = 0.0
        if self._fist_since is not None and self._last_seen is not None:
            prog = self.fist_progress(self._last_seen)
        tip = self._last_tip if visible else None
        return PenSnapshot(pen=pen, gesture=gesture, hand_visible=visible, fist_progress=prog,
                           tip=tip, message=message, events=events)
