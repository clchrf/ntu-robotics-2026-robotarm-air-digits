"""模仿 movearm/arm_controller/arm_controller.ino 行為的假控制器（僅供測試）。

依韌體原始碼重現：
- 開埠後印出「System Ready」。
- 解析「a,b,c,d」：少於 3 個逗號的行會被忽略（韌體同樣不回應）。
- 步數目標 = 角度 × 8.888…；所有軸同時每 1.2 ms 走一步；新目標可在移動中覆寫。
- 由移動轉為停止時印一次「OK」；目標等於現況時不回應。
另可注入故障：不回 OK、第 N 個指令後斷線、第 N 個指令後控制器重開、錯誤開機訊息。
"""

from __future__ import annotations

import threading
import time
from typing import List, Optional

from . import movearm_protocol as mp
from .serial_link import LineLink


class FakeMovearmLink(LineLink):
    def __init__(self, time_scale: float = 1.0, banner: Optional[str] = mp.BOOT_BANNER,
                 drop_ok: bool = False, disconnect_after: Optional[int] = None,
                 reset_after: Optional[int] = None, fail_open: Optional[str] = None,
                 boot_delay_s: float = 0.02):
        super().__init__()
        self.description = 'FAKE'
        self.time_scale = time_scale
        self.banner = banner
        self.drop_ok = drop_ok
        self.disconnect_after = disconnect_after
        self.reset_after = reset_after
        self.fail_open = fail_open
        self.boot_delay_s = boot_delay_s
        self.received: List[str] = []
        self.steps = [0, 0, 0]
        self.servo = 90.0
        self._target = [0, 0, 0]
        self._start = [0, 0, 0]
        self._t0 = 0.0
        self._moving = False
        self._open = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.ok_count = 0

    @property
    def is_open(self) -> bool:
        return self._open and self._broken is None

    def open(self) -> None:
        from .serial_link import LinkError
        if self.fail_open:
            raise LinkError(self.fail_open)
        self._open = True
        threading.Thread(target=self._loop, daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        self._open = False
        self._mark_broken('Closed')

    def _period(self) -> float:
        return mp.STEP_PERIOD_S * self.time_scale

    def _position(self, now: float) -> List[int]:
        done = int((now - self._t0) / self._period())
        pos = []
        for s, t in zip(self._start, self._target):
            d = t - s
            pos.append(s + (min(abs(d), done) * (1 if d >= 0 else -1)))
        return pos

    def _loop(self):
        if self.boot_delay_s:
            time.sleep(self.boot_delay_s)
        if self.banner is not None:
            self._q.put(self.banner)
        while not self._stop.is_set():
            with self._lock:
                if self._moving:
                    now = time.monotonic()
                    self.steps = self._position(now)
                    if self.steps == self._target:
                        self._moving = False
                        if not self.drop_ok:
                            self.ok_count += 1
                            self._q.put(mp.DONE_REPLY)
            time.sleep(0.001)

    def write_line(self, text: str) -> None:
        from .serial_link import LinkError
        if not self.is_open:
            raise LinkError(self._broken or 'Not open')
        self.received.append(text)
        n = len(self.received)
        if self.disconnect_after is not None and n > self.disconnect_after:
            self._open = False
            self._mark_broken('Simulated disconnect')
            raise LinkError('Simulated disconnect')
        if self.reset_after is not None and n > self.reset_after:
            with self._lock:
                self.steps = [0, 0, 0]
                self._target = [0, 0, 0]
                self._moving = False
            self._q.put(mp.BOOT_BANNER)
            return
        parts = text.split(',')
        if len(parts) < 4:
            return  # 韌體：找不到三個逗號就忽略
        try:
            vals = [float(p) for p in parts[:4]]
        except ValueError:
            return
        with self._lock:
            now = time.monotonic()
            cur = self._position(now) if self._moving else list(self.steps)
            target = [round(v * mp.STEPS_PER_DEGREE) for v in vals[:3]]
            if abs(vals[3] - self.servo) > 1.0:
                self.servo = vals[3]
            self._start = cur
            self._target = target
            self._t0 = now
            self.steps = cur
            self._moving = cur != target
