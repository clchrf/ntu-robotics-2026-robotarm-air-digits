"""動作執行：模擬後端、movearm 協定、取消與故障處理（以依韌體原始碼撰寫的假控制器測試）。"""

import threading
import time

import pytest

from digit_arm.core import frame_protocol, movearm_protocol as mp
from digit_arm.core.fake_movearm import FakeMovearmLink
from digit_arm.core.motion import (BackendError, MotionSettings, MovearmSerialArm, RepeatRunner, SimulatedArm,
                                   validate_hardware_settings)

FAST = dict(dwell_s=0.01, pause_s=0.01)


def test_packet_format_matches_movearm():
    # movearm.py：f"{a0:.1f},{-a1:.1f},{-a2:.1f},{a3:.1f}"
    assert mp.format_packet((0, 0, 0, 90)) == '0.0,0.0,0.0,90.0'
    assert mp.format_packet((5, 10, 20, 90)) == '5.0,-10.0,-20.0,90.0'
    assert mp.parse_packet('5.0,-10.0,-20.0,90.0') == (5.0, 10.0, 20.0, 90.0)
    assert 90 < mp.NOMINAL_SPEED_DEG_S < 95


@pytest.mark.parametrize('joint,delta,ok', [
    ('', 0.0, False), ('base', 0.0, False), ('base', 5.0, True), ('base', -5.0, True),
    ('arm', 8.0, True), ('arm', -8.0, False), ('forearm', -3.0, False), ('base', 50.0, False),
    ('forearm', 30.0, True),
    ('wrist', 5.0, False),
])
def test_hardware_settings_validation(joint, delta, ok):
    assert (validate_hardware_settings(MotionSettings(joint=joint, delta_deg=delta)) == []) is ok


def _progress_log():
    log = []
    return log, lambda d, t, ph: log.append((d, t, ph))


def test_simulation_completes_all_reps():
    s = MotionSettings('base', 10, **FAST)
    arm = SimulatedArm(s, sleep=lambda _x: None)
    log, cb = _progress_log()
    res = RepeatRunner(arm, s).run(3, lambda: False, cb)
    assert res.outcome == 'succeeded' and res.completed == 3 and 'simulation' in res.message
    assert arm.log == ['out', 'home'] * 3
    assert [x for x in log if x[2] == 'pause'] == [(1, 3, 'pause'), (2, 3, 'pause'), (3, 3, 'pause')]


def test_simulation_cancel_stops_further_reps_and_returns_home():
    s = MotionSettings('base', 10, dwell_s=0.05, pause_s=0.05)
    arm = SimulatedArm(s, sleep=lambda _x: time.sleep(0.02))
    cancel = threading.Event()
    log, cb = _progress_log()

    def watch(d, t, ph):
        cb(d, t, ph)
        if d == 2 and ph == 'out':
            cancel.set()
    res = RepeatRunner(arm, s).run(10, cancel.is_set, watch)
    assert res.outcome == 'canceled' and res.completed == 2
    assert arm.log[-1] == 'home' and arm.log.count('out') == 3
    assert log[-1][2] == 'cancel_return'


def _hw(link, **kw):
    s = MotionSettings('base', 5.0, smooth=kw.get('smooth', False), **FAST)
    return MovearmSerialArm(lambda: link, s, boot_timeout_s=kw.get('boot', 1.0)), s


def test_hardware_refuses_without_confirmed_motion_and_opens_nothing():
    opened = []
    arm = MovearmSerialArm(lambda: opened.append(1), MotionSettings())
    with pytest.raises(BackendError, match='No motion set'):
        arm.connect()
    assert opened == []


def test_hardware_run_sends_only_home_and_out_and_waits_for_ok():
    link = FakeMovearmLink(time_scale=0.2)
    arm, s = _hw(link)
    arm.connect()
    assert arm.connected and arm.position_trusted
    res = RepeatRunner(arm, s).run(3, lambda: False, lambda *a: None)
    assert res.outcome == 'succeeded' and res.completed == 3 and 'simulation' not in res.message
    assert link.received == ['5.0,0.0,0.0,90.0', '0.0,0.0,0.0,90.0'] * 3
    assert link.ok_count == 6 and link.steps == [0, 0, 0]


def test_hardware_cancel_during_out_returns_home_and_sends_nothing_more():
    link = FakeMovearmLink(time_scale=2.0)
    arm, s = _hw(link)
    arm.connect()
    cancel = threading.Event()

    def cb(d, t, ph):
        if d == 1 and ph == 'out':
            cancel.set()
    res = RepeatRunner(arm, s).run(10, cancel.is_set, cb)
    assert res.outcome == 'canceled' and res.completed == 1
    assert link.received == ['5.0,0.0,0.0,90.0', '0.0,0.0,0.0,90.0'] * 2
    time.sleep(0.1)
    assert len(link.received) == 4 and link.steps == [0, 0, 0]


def test_missing_boot_banner_is_refused():
    link = FakeMovearmLink(banner=None)
    arm, _ = _hw(link, boot=0.3)
    with pytest.raises(BackendError, match='System Ready'):
        arm.connect()
    assert link.received == [] and not arm.connected


def test_foreign_firmware_is_refused():
    link = FakeMovearmLink(banner='READY')
    arm, _ = _hw(link)
    with pytest.raises(BackendError, match='drawing firmware'):
        arm.connect()
    assert link.received == []


def test_no_ok_times_out_and_locks_position():
    link = FakeMovearmLink(time_scale=0.2, drop_ok=True)
    arm, s = _hw(link)
    s.ok_timeout_margin_s = 0.2
    arm.connect()
    res = RepeatRunner(arm, s).run(3, lambda: False, lambda *a: None)
    assert res.outcome == 'aborted' and res.completed == 0 and 'OK' in res.message
    assert not arm.position_trusted
    with pytest.raises(BackendError, match='Position unknown'):
        arm.move_out()
    assert len(link.received) == 1


def test_disconnect_mid_run_aborts():
    link = FakeMovearmLink(time_scale=0.2, disconnect_after=3)
    arm, s = _hw(link)
    arm.connect()
    res = RepeatRunner(arm, s).run(5, lambda: False, lambda *a: None)
    assert res.outcome == 'aborted' and res.completed == 1 and 'lost' in res.message
    assert not arm.connected and not arm.position_trusted


def test_controller_reset_mid_run_aborts():
    link = FakeMovearmLink(time_scale=0.2, reset_after=2)
    arm, s = _hw(link)
    arm.connect()
    res = RepeatRunner(arm, s).run(5, lambda: False, lambda *a: None)
    assert res.outcome == 'aborted' and 'restarted' in res.message
    assert not arm.position_trusted


def test_frame_protocol_roundtrip_and_errors():
    import io
    data = frame_protocol.pack({'type': 'frame', 'seq': 1, 'hands': []}, b'\xff\xd8jpeg') + \
        frame_protocol.pack({'type': 'error', 'message': '鏡頭中斷'})
    s = io.BytesIO(data)
    assert frame_protocol.read_packet(s) == ({'type': 'frame', 'seq': 1, 'hands': []}, b'\xff\xd8jpeg')
    assert frame_protocol.read_packet(s)[0]['message'] == '鏡頭中斷'
    assert frame_protocol.read_packet(s) is None
    assert frame_protocol.read_packet(io.BytesIO(data[:10])) is None
    with pytest.raises(ValueError):
        frame_protocol.read_packet(io.BytesIO(b'XXXX' + data[4:]))


def test_smooth_motion_streams_only_points_between_home_and_target():
    link = FakeMovearmLink(time_scale=1.0)
    s = MotionSettings('forearm', 30.0, smooth=True, stream_hz=125.0, max_speed_deg_s=25.0,
                       max_step_deg=0.25, **FAST)
    arm = MovearmSerialArm(lambda: link, s, boot_timeout_s=1.0)
    arm.connect()
    t0 = time.monotonic()
    res = RepeatRunner(arm, s).run(1, lambda: False, lambda *a: None)
    took = time.monotonic() - t0
    assert res.outcome == 'succeeded' and res.completed == 1, res.message
    forearm = [mp.parse_packet(p)[2] for p in link.received]
    others = [mp.parse_packet(p)[:2] + (mp.parse_packet(p)[3],) for p in link.received]
    assert all(0.0 <= v <= 30.0 for v in forearm)           # 中間點只在 HOME 與 +30° 之間
    assert all(o == (0.0, 0.0, 90.0) for o in others)       # 其他軸與腕不動
    ends = [v for v in forearm if v in (0.0, 30.0)]
    assert ends == [30.0, 0.0]                              # 每半程最後都精確停在端點
    steps = [abs(b - a) for a, b in zip([0.0] + forearm, forearm)]
    assert max(steps) <= 0.25 + 1e-6                        # 每個指令最多 0.25°（不失步）
    assert link.steps == [0, 0, 0]
    assert 3.5 < took < 7.0                                 # 單程約 2.4 秒
    assert arm.position_trusted


def test_step_cap_validation():
    bad = MotionSettings('forearm', 30.0, smooth=True, max_step_deg=0.1)
    assert any('max_step_deg' in e for e in validate_hardware_settings(bad))
    too_fast = MotionSettings('forearm', 30.0, smooth=True, max_speed_deg_s=120.0)
    assert any('max_speed_deg_s' in e for e in validate_hardware_settings(too_fast))


def test_smooth_values_shape():
    from digit_arm.core.motion import smooth_values
    s = MotionSettings('forearm', 30.0, smooth=True, stream_hz=125.0, max_speed_deg_s=25.0, max_step_deg=0.25)
    v = smooth_values(0.0, 30.0, s)
    assert v[-1] == 30.0 and all(b > a for a, b in zip(v, v[1:]))
    assert max(abs(b - a) for a, b in zip([0.0] + v, v)) <= 0.25 + 1e-6
    assert abs(30.0 - v[-2]) >= 0.12
    back = smooth_values(30.0, 0.0, s)
    assert back[-1] == 0.0 and all(b < a for a, b in zip(back, back[1:]))


def test_smooth_cancel_mid_move_turns_back_immediately():
    """取消時不必等半程走完：立刻停止送點、從半路平滑折返 HOME，且之後不再送任何點。"""
    link = FakeMovearmLink(time_scale=1.0)
    s = MotionSettings('forearm', 30.0, smooth=True, stream_hz=125.0, max_speed_deg_s=25.0,
                       max_step_deg=0.25, **FAST)
    arm = MovearmSerialArm(lambda: link, s, boot_timeout_s=1.0)
    arm.connect()
    cancel = threading.Event()
    threading.Timer(0.6, cancel.set).start()      # 出發 0.6 秒後取消（單程約 1.8 秒）
    t0 = time.monotonic()
    res = RepeatRunner(arm, s).run(5, cancel.is_set, lambda *a: None)
    took = time.monotonic() - t0
    forearm = [mp.parse_packet(p)[2] for p in link.received]
    assert res.outcome == 'canceled' and res.completed == 0, res.message
    peak = max(forearm)
    assert 1.0 < peak < 25.0                        # 停在半路，沒有走到 30°
    assert forearm[-1] == 0.0 and link.steps == [0, 0, 0]
    assert forearm.index(peak) < len(forearm) - 1 and all(
        b <= a for a, b in zip(forearm[forearm.index(peak):], forearm[forearm.index(peak) + 1:]))  # 之後只往回
    assert took < 2.5
    n = len(link.received)
    time.sleep(0.2)
    assert len(link.received) == n                  # 取消後不再送任何點
    assert arm.position_trusted


def test_firmware_accel_timing():
    s = MotionSettings('forearm', 90.0, smooth=False, fw_max_speed_deg_s=60.0, fw_accel_deg_s2=120.0)
    assert abs(s.half_move_s() - (90 / 60 + 60 / 120)) < 1e-9      # 2.0 秒（韌體模擬為 1.93 秒）
    assert abs(s.move_time(10.0) - 2 * (10 / 120) ** 0.5) < 1e-9   # 短距離：三角形曲線
    old = MotionSettings('forearm', 90.0, smooth=False)
    assert abs(old.half_move_s() - 90 / mp.NOMINAL_SPEED_DEG_S) < 1e-9


def test_negative_forearm_requires_explicit_override():
    assert validate_hardware_settings(MotionSettings('forearm', -90.0, max_delta_deg=90.0))
    ok = MotionSettings('forearm', -90.0, max_delta_deg=90.0, allow_beyond_movearm_limits=True)
    assert validate_hardware_settings(ok) == []
    too_big = MotionSettings('forearm', -120.0, max_delta_deg=90.0, allow_beyond_movearm_limits=True)
    assert validate_hardware_settings(too_big)          # 仍受 max_delta_deg 限制


def test_negative_forearm_packets():
    link = FakeMovearmLink(time_scale=0.2)
    s = MotionSettings('forearm', -90.0, smooth=False, max_delta_deg=90.0,
                       allow_beyond_movearm_limits=True, **FAST)
    arm = MovearmSerialArm(lambda: link, s, boot_timeout_s=1.0)
    arm.connect()
    res = RepeatRunner(arm, s).run(1, lambda: False, lambda *a: None)
    assert res.outcome == 'succeeded'
    # movearm 慣例：前臂角度送出時取負號 → 介面 -90° 送出 +90.0
    assert link.received == ['0.0,0.0,90.0,90.0', '0.0,0.0,0.0,90.0']
    assert link.steps == [0, 0, 0]


def test_arm_and_forearm_move_together():
    """依繪圖校正點 (Arm 19, Forearm 15)：兩軸同時轉出、同時回到起點。"""
    link = FakeMovearmLink(time_scale=0.2)
    s = MotionSettings(deltas=(0.0, 19.0, 15.0), smooth=False, **FAST)
    assert validate_hardware_settings(s) == []
    assert s.describe() == 'Arm +19°, Forearm +15°, then back'
    arm = MovearmSerialArm(lambda: link, s, boot_timeout_s=1.0)
    arm.connect()
    res = RepeatRunner(arm, s).run(2, lambda: False, lambda *a: None)
    assert res.outcome == 'succeeded' and res.completed == 2
    # movearm 慣例：大臂、前臂取負號送出
    assert link.received == ['0.0,-19.0,-15.0,90.0', '0.0,0.0,0.0,90.0'] * 2
    assert link.steps == [0, 0, 0]


def test_multi_joint_smooth_points_stay_on_segment():
    link = FakeMovearmLink(time_scale=1.0)
    s = MotionSettings(deltas=(0.0, 19.0, 15.0), smooth=True, stream_hz=125.0, max_speed_deg_s=25.0,
                       max_step_deg=0.25, **FAST)
    arm = MovearmSerialArm(lambda: link, s, boot_timeout_s=1.0)
    arm.connect()
    RepeatRunner(arm, s).run(1, lambda: False, lambda *a: None)
    for pkt in link.received:
        _, a, f, w = mp.parse_packet(pkt)
        assert 0.0 <= a <= 19.0 and 0.0 <= f <= 15.0 and w == 90.0
        assert abs(f - a * 15.0 / 19.0) <= 0.11          # 兩軸按比例一起走（封包精度 0.1°）
    assert link.steps == [0, 0, 0]


def test_multi_joint_validation():
    assert validate_hardware_settings(MotionSettings(deltas=(0.0, -5.0, 10.0)))       # 大臂負方向超出範圍
    assert validate_hardware_settings(MotionSettings(deltas=(0.0, 60.0, 10.0)))       # 超過 max_delta 45
    assert validate_hardware_settings(MotionSettings()) == ['No motion set (motion_arm_deg / motion_forearm_deg, at least 1°)']
