"""重複小幅動作：出去 → 停留 → 回到起點 = 一下。

後端
- SimulatedArm：只計時，不碰任何硬體。所有訊息都標示「simulation」。
- MovearmSerialArm：movearm arm_controller 協定（見 movearm_protocol.py）。
  * 連線當下的姿勢就是起點（開埠會重設 Arduino，當下姿勢即為 0 步）。
  * 動作點 = 起點 + 各軸設定的角度（可同時動大臂與前臂）。送出的每一點都落在
    「起點 → 動作點」這條線段上，每半程最後精確停在端點。
  * 每半程都等控制器回「OK」（步進停止）才繼續；逾時、斷線、控制器重開都立即停止，
    並把位置標成不可信，必須人工擺回起點後重新連線。

取消（RepeatRunner）
- 不會再開始新的一下，也不會送出任何未執行的動作。
- 若手臂正在出去：立刻停止送點，停在目前位置後折返起點。
- 若在停留中：立刻回起點。回起點後結束，該下不計入完成數。

使用者看得到的訊息一律為簡單英文（介面語言）。
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from . import movearm_protocol as mp
from .serial_link import LineLink, LinkError

MIN_DELTA_DEG = 1.0           # 太小時控制器可能不動也不回 OK
MIN_WAYPOINT_STEP_DEG = 0.12  # 相鄰兩點至少差 1 步（0.1125°），確保最後一點一定會動、會回 OK
PACKET_RESOLUTION_DEG = 0.1   # movearm 封包格式為小數一位

JOINT_NAMES_EN = {'base': 'Base', 'arm': 'Arm', 'forearm': 'Forearm'}


class BackendError(Exception):
    pass


@dataclass
class MotionSettings:
    # 舊的單軸設定（motion_joint / motion_delta_deg），deltas 全為 0 時使用
    joint: str = ''
    delta_deg: float = 0.0
    # 多軸設定（base, arm, forearm），以 movearm 介面的角度表示
    deltas: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    dwell_s: float = 0.3
    pause_s: float = 0.25
    speed_deg_s: float = mp.NOMINAL_SPEED_DEG_S
    ok_timeout_factor: float = 4.0
    ok_timeout_margin_s: float = 1.5
    max_delta_deg: float = 45.0      # 軟體上限，不是已驗證的安全範圍
    # 平滑移動（主機分段送點，每點最多 max_step_deg）；原版韌體沒有加減速時使用
    smooth: bool = True
    stream_hz: float = 125.0
    max_speed_deg_s: float = 25.0
    max_step_deg: float = 0.25
    # 板子上若是「加減速版」arm_controller.ino，填入它的最高速度與加速度（預估時間與逾時用）；原版填 0
    fw_max_speed_deg_s: float = 0.0
    fw_accel_deg_s2: float = 0.0
    # 允許超出 movearm 介面的角度範圍；只有使用者確認過該方向路徑安全才開啟
    allow_beyond_movearm_limits: bool = False

    def out_deltas(self) -> Tuple[float, float, float]:
        if any(abs(d) > 0 for d in self.deltas):
            return tuple(float(d) for d in self.deltas)  # type: ignore[return-value]
        d = [0.0, 0.0, 0.0]
        if self.joint in mp.JOINTS:
            d[mp.JOINTS.index(self.joint)] = float(self.delta_deg)
        return tuple(d)  # type: ignore[return-value]

    def span(self) -> float:
        """動最多的那一軸的角度（用來決定時間與分段）。"""
        return max(abs(d) for d in self.out_deltas())

    def move_time(self, distance_deg: float) -> float:
        """韌體走完 distance_deg 的時間（秒）。"""
        d = abs(distance_deg)
        v, a = self.fw_max_speed_deg_s, self.fw_accel_deg_s2
        if v > 0 and a > 0:
            if d >= v * v / a:                     # 梯形：加速、等速、減速
                return d / v + v / a
            return 2.0 * math.sqrt(d / a)          # 三角形：還沒到最高速就要減速
        return d / max(self.speed_deg_s, 1e-6)

    def half_move_s(self) -> float:
        """單程預估時間（秒）。"""
        d = self.span()
        if self.smooth:
            return smooth_point_count(d, self) / max(self.stream_hz, 1e-6)
        return self.move_time(d)

    def describe(self) -> str:
        parts = [f'{JOINT_NAMES_EN[j]} {d:+.0f}°' for j, d in zip(mp.JOINTS, self.out_deltas()) if abs(d) > 0]
        if not parts:
            return 'No motion set'
        return ', '.join(parts) + ', then back'


def validate_hardware_settings(s: MotionSettings) -> List[str]:
    errors = []
    deltas = s.out_deltas()
    if s.span() < MIN_DELTA_DEG:
        errors.append(f'No motion set (motion_arm_deg / motion_forearm_deg, at least {MIN_DELTA_DEG:g}°)')
        return errors
    for j, d in zip(mp.JOINTS, deltas):
        if abs(d) == 0:
            continue
        name = JOINT_NAMES_EN[j]
        if abs(d) > s.max_delta_deg:
            errors.append(f'{name} {d:g}° is over the limit ±{s.max_delta_deg:g}° (max_delta_deg)')
        lo, hi = mp.UI_LIMITS[j]
        target = mp.HOME_UI[mp.JOINTS.index(j)] + d
        if not lo <= target <= hi and not s.allow_beyond_movearm_limits:
            errors.append(f'{name} target {target:g}° is outside the movearm range {lo:g} to {hi:g}° '
                          '(turn on allow_beyond_movearm_limits only if that side is clear)')
    if s.smooth and (s.stream_hz <= 0 or s.max_speed_deg_s <= 0):
        errors.append('Smooth motion needs stream_hz and max_speed_deg_s above 0')
    if s.smooth and s.max_speed_deg_s > mp.NOMINAL_SPEED_DEG_S:
        errors.append(f'max_speed_deg_s must be at most about {mp.NOMINAL_SPEED_DEG_S:.0f}°/s')
    if s.smooth and s.max_step_deg < 0.2:
        errors.append('max_step_deg must be at least 0.2° (commands use 0.1° steps)')
    if s.dwell_s < 0 or s.pause_s < 0:
        errors.append('Wait times cannot be negative')
    return errors


def smooth_point_count(distance: float, settings: MotionSettings) -> int:
    """單程要送幾個點：同時滿足最高速度與「四捨五入到 0.1° 後每點仍不超過 max_step_deg」。"""
    d = abs(distance)
    raw_cap = max(settings.max_step_deg - PACKET_RESOLUTION_DEG, 0.05)
    by_speed = 1.5 * d / max(settings.max_speed_deg_s, 1e-6) * settings.stream_hz  # smoothstep 峰值 = 1.5 × 平均
    by_step = 1.5 * d / raw_cap
    return max(1, int(math.ceil(by_speed)), int(math.ceil(by_step)))


def smooth_values(start: float, end: float, settings: MotionSettings) -> List[float]:
    """start → end 的平滑中間值（不含 start，最後一個必為 end）。

    smoothstep 3t²−2t³：起點與終點速度為 0，中間最快。每點四捨五入到封包精度 0.1°，
    相鄰兩點不超過 max_step_deg；最後一段至少 0.2°（≥1 步），確保馬達會動、控制器會回 OK。
    """
    d = end - start
    n = smooth_point_count(d, settings)
    out: List[float] = []
    last = round(start, 1)
    for i in range(1, n):
        t = i / n
        v = round(start + d * (3 * t * t - 2 * t * t * t), 1)
        if v != last:
            out.append(v)
            last = v
    while out and abs(end - out[-1]) < 0.2 - 1e-9:
        out.pop()
    out.append(end)
    return out


class ArmBackend:
    simulated = True
    kind = 'base'

    def connect(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    @property
    def connected(self) -> bool:
        raise NotImplementedError

    @property
    def position_trusted(self) -> bool:
        return True

    @property
    def port_description(self) -> str:
        return ''

    @property
    def status_message(self) -> str:
        return ''

    def move_out(self, stop: Optional[Callable[[], bool]] = None) -> None:
        raise NotImplementedError

    def move_home(self) -> None:
        raise NotImplementedError


class SimulatedArm(ArmBackend):
    simulated = True
    kind = 'simulation'

    def __init__(self, settings: MotionSettings, sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self._sleep = sleep
        self._connected = True
        self._frac = 0.0
        self.log: List[str] = []

    def connect(self) -> None:
        self._connected = True

    def close(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def port_description(self) -> str:
        return 'Simulation (no serial port)'

    @property
    def status_message(self) -> str:
        return 'Simulation: the arm will not move'

    def move_out(self, stop: Optional[Callable[[], bool]] = None) -> None:
        """模擬出發；stop() 為真時停在半路（記住走了多少）。"""
        self.log.append('out')
        total = self.settings.half_move_s()
        steps = max(1, int(total / 0.02))
        self._frac = 0.0
        for i in range(steps):
            if stop is not None and stop():
                break
            self._sleep(total / steps)
            self._frac = (i + 1) / steps

    def move_home(self) -> None:
        self.log.append('home')
        self._sleep(self.settings.half_move_s() * self._frac)
        self._frac = 0.0


class MovearmSerialArm(ArmBackend):
    simulated = False
    kind = 'hardware'

    def __init__(self, link_factory: Callable[[], LineLink], settings: MotionSettings,
                 boot_timeout_s: float = 5.0, log: Optional[Callable[[str], None]] = None):
        self._factory = link_factory
        self.settings = settings
        self.boot_timeout_s = boot_timeout_s
        self._log = log or (lambda _m: None)
        self._link: Optional[LineLink] = None
        self._trusted = False
        self._message = 'Not connected'
        self._lock = threading.Lock()
        self._home = mp.HOME_UI
        d = settings.out_deltas()
        self._out = tuple(self._home[i] + d[i] for i in range(3)) + (self._home[3],)
        self._frac = 0.0         # 目前位置：0 = 起點、1 = 動作點（沿線段）
        self.sent: List[str] = []

    # ── 狀態 ──
    @property
    def connected(self) -> bool:
        return self._link is not None and self._link.is_open

    @property
    def position_trusted(self) -> bool:
        return self.connected and self._trusted

    @property
    def port_description(self) -> str:
        return self._link.description if self._link else ''

    @property
    def status_message(self) -> str:
        if self._link is not None and not self._link.is_open and self._link.broken_reason:
            if self._trusted:
                self._trusted = False
                self._message = f'Serial link lost ({self._link.broken_reason}). Put the arm back and reconnect.'
        return self._message

    # ── 連線 ──
    def connect(self) -> None:
        errors = validate_hardware_settings(self.settings)
        if errors:
            self._message = '; '.join(errors)
            raise BackendError(self._message)
        self.close()
        link = self._factory()
        try:
            link.open()
        except LinkError as exc:
            self._message = str(exc)
            raise BackendError(self._message) from exc
        self._link = link
        deadline = time.monotonic() + self.boot_timeout_s
        try:
            while time.monotonic() < deadline:
                line = link.read_line(deadline - time.monotonic())
                if line is None:
                    break
                if line == mp.BOOT_BANNER:
                    self._trusted = True
                    self._frac = 0.0
                    self._message = f'Connected on {link.description} (start = pose when connected)'
                    self._log(self._message)
                    return
                if line in mp.FOREIGN_BANNERS:
                    raise BackendError(f'Controller replied "{line}": this is the drawing firmware, '
                                       'not movearm arm_controller. Stopped.')
                self._log(f'[controller] {line}')
        except LinkError as exc:
            self.close()
            self._message = f'Connection lost: {exc}'
            raise BackendError(self._message) from exc
        except BackendError as exc:
            self.close()
            self._message = str(exc)
            raise
        self.close()
        self._message = (f'No "{mp.BOOT_BANNER}" within {self.boot_timeout_s:g} s, so this may not be the '
                         'movearm firmware. Nothing was sent.')
        raise BackendError(self._message)

    def close(self) -> None:
        link, self._link = self._link, None
        self._trusted = False
        if link is not None:
            link.close()
            self._message = 'Disconnected'

    # ── 動作 ──
    def move_out(self, stop: Optional[Callable[[], bool]] = None) -> None:
        """往動作點移動；stop() 為真時立刻停止送點，停在目前位置（之後 move_home 從這裡回去）。"""
        self._move(1.0, stop)

    def move_home(self) -> None:
        self._move(0.0, None)

    def _pose_at(self, frac: float):
        return tuple(self._home[i] + frac * (self._out[i] - self._home[i]) for i in range(3)) + (self._home[3],)

    def _move(self, target_frac: float, stop: Optional[Callable[[], bool]]) -> None:
        with self._lock:
            link = self._link
            if link is None or not link.is_open:
                self._trusted = False
                raise BackendError('Serial port not connected')
            if not self._trusted:
                raise BackendError('Position unknown. Put the arm back at the start pose and reconnect.')
            span = self.settings.span()
            start = self._frac * span
            target = target_frac * span
            if abs(target - start) < MIN_WAYPOINT_STEP_DEG:
                return  # 已在目標（例如取消時還沒出發）：送重複目標韌體不會回 OK
            values = smooth_values(start, target, self.settings) if self.settings.smooth else [target]
            period = 1.0 / self.settings.stream_hz if self.settings.smooth else 0.0
            try:
                for stale in link.drain():
                    self._check_line(stale, stale_ok=True)
                # 每一點都落在「起點 → 動作點」線段上；小段完成的 OK 記下但不等待
                sent_v, prev_v, got_ok = start, start, False
                next_t = time.monotonic()
                for k, v in enumerate(values):
                    if k > 0 and stop is not None and stop():
                        break  # 取消：不再送點，停在最後送出的位置
                    frac = v / span
                    if not -1e-9 <= frac <= 1 + 1e-9:
                        raise BackendError('Internal error: point outside the checked path')
                    self._send(link, self._pose_at(min(1.0, max(0.0, frac))))
                    prev_v, sent_v, got_ok = sent_v, v, False
                    next_t += period
                    if k < len(values) - 1:
                        got_ok = self._read_until(link, next_t, ignore_ok=True)
                # 等最後一點到位（控制器回 OK）
                expected = self.settings.move_time(abs(sent_v - prev_v))
                timeout = expected * self.settings.ok_timeout_factor + self.settings.ok_timeout_margin_s
                if not got_ok and not self._read_until(link, time.monotonic() + timeout, ignore_ok=False):
                    self._trusted = False
                    self._message = (f'No "OK" from the controller within {timeout:.1f} s. Stopped. '
                                     'Check the arm, put it back and reconnect.')
                    raise BackendError(self._message)
                # 若剛剛的 OK 其實是前一小段遲到的回覆，真正的 OK 會在這段時間內到達：一併吸收
                self._read_until(link, time.monotonic() + expected + 0.08, ignore_ok=True)
                self._frac = sent_v / span
            except LinkError as exc:
                self._trusted = False
                self._message = f'Serial link lost ({exc}). Put the arm back and reconnect.'
                raise BackendError(self._message) from exc

    def _send(self, link: LineLink, pose) -> None:
        packet = mp.format_packet(pose)
        link.write_line(packet)
        self.sent.append(packet)

    def _read_until(self, link: LineLink, deadline: float, ignore_ok: bool) -> bool:
        """讀取回覆直到 deadline，回傳期間是否收到 OK。
        ignore_ok=False 時收到 OK 立即返回；ignore_ok=True 時讀到 deadline 為止。開機訊息一律視為錯誤。"""
        got = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return got
            line = link.read_line(min(remaining, 0.05))
            if line is None:
                continue
            if self._check_line(line):
                got = True
                if not ignore_ok:
                    return True

    def _check_line(self, line: str, stale_ok: bool = False) -> bool:
        if line == mp.DONE_REPLY:
            return not stale_ok
        if line == mp.BOOT_BANNER or line in mp.FOREIGN_BANNERS:
            self._trusted = False
            self._message = 'Controller restarted, so its position was reset. Put the arm back and reconnect.'
            raise BackendError(self._message)
        if line:
            self._log(f'[controller] {line}')
        return False


@dataclass
class RunResult:
    outcome: str        # succeeded / canceled / aborted
    completed: int
    requested: int
    message: str


ProgressFn = Callable[[int, int, str], None]


def _sim_tag(backend: ArmBackend) -> str:
    return ' (simulation)' if backend.simulated else ''


class RepeatRunner:
    def __init__(self, backend: ArmBackend, settings: MotionSettings,
                 wait: Optional[Callable[[float, Callable[[], bool]], bool]] = None):
        self.backend = backend
        self.settings = settings
        self._wait = wait or _wait_or_cancel

    def run(self, count: int, cancel_requested: Callable[[], bool], on_progress: ProgressFn) -> RunResult:
        done = 0
        sim = _sim_tag(self.backend)
        try:
            for _ in range(count):
                if cancel_requested():
                    return RunResult('canceled', done, count, f'Canceled{sim}: {done} of {count} done')
                on_progress(done, count, 'out')
                self.backend.move_out(cancel_requested)   # 取消時停在半路，下面立刻折返
                if cancel_requested():
                    return self._cancel_return(done, count, on_progress)
                on_progress(done, count, 'dwell')
                if self._wait(self.settings.dwell_s, cancel_requested):
                    return self._cancel_return(done, count, on_progress)
                on_progress(done, count, 'return')
                self.backend.move_home()
                done += 1
                on_progress(done, count, 'pause')
                if done < count and self._wait(self.settings.pause_s, cancel_requested):
                    return RunResult('canceled', done, count, f'Canceled{sim}: {done} of {count} done')
        except BackendError as exc:
            return RunResult('aborted', done, count, f'Stopped: {exc} ({done} of {count} done)')
        return RunResult('succeeded', done, count, f'Done: {done} of {count}{sim}')

    def _cancel_return(self, done: int, count: int, on_progress: ProgressFn) -> RunResult:
        sim = _sim_tag(self.backend)
        on_progress(done, count, 'cancel_return')
        self.backend.move_home()
        return RunResult('canceled', done, count, f'Canceled and back at start{sim}: {done} of {count} done')


def _wait_or_cancel(seconds: float, cancel_requested: Callable[[], bool]) -> bool:
    """等待 seconds 秒；期間要求取消就回傳 True。"""
    end = time.monotonic() + seconds
    while True:
        if cancel_requested():
            return True
        remaining = end - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.02, remaining))
