"""介面狀態：按鈕依狀態啟用或停用。"""

from digit_arm.ui import state as U


def _ready_state():
    st = U.UiState()
    st.camera = {'state': 'streaming', 'message': '', 'source': '', 'fps': 30}
    st.arm = {'mode': 'simulation', 'simulated': True, 'ready': True, 'connected': True}
    st.executor_online = True
    st.ink = {'ink_id': 4, 'locked': True, 'pen_down': False, 'strokes': [[(0, 0), (0, 1)]]}
    return st


def test_writing_start_disabled():
    st = _ready_state()
    st.ink['locked'] = False
    b = st.buttons()
    assert st.phase() == U.WRITING and not b.start and not b.cancel and b.clear


def test_recognizing_then_ok_enables_start_with_count():
    st = _ready_state()
    assert st.phase() == U.RECOGNIZING and not st.buttons().start
    st.recognition = {'ink_id': 4, 'success': True, 'value': 10, 'text': '10', 'reason': ''}
    b = st.buttons()
    assert st.phase() == U.RESULT_OK and b.start and b.start_text == 'Start (10 times)'
    assert st.headline().title == 'You wrote 10'


def test_stale_recognition_is_ignored():
    st = _ready_state()
    st.recognition = {'ink_id': 3, 'success': True, 'value': 2}
    assert st.phase() == U.RECOGNIZING and not st.buttons().start


def test_failure_never_enables_start():
    st = _ready_state()
    st.recognition = {'ink_id': 4, 'success': False, 'value': 0, 'reason': '無法確定'}
    assert st.phase() == U.RESULT_FAIL and not st.buttons().start and st.buttons().clear


def test_start_requires_ready_executor():
    st = _ready_state()
    st.recognition = {'ink_id': 4, 'success': True, 'value': 3}
    st.arm['ready'] = False
    assert not st.buttons().start
    st.arm['ready'] = True
    st.executor_online = False
    assert not st.buttons().start


def test_running_and_cancel_buttons():
    st = _ready_state()
    st.recognition = {'ink_id': 4, 'success': True, 'value': 3}
    st.running = True
    st.progress = {'completed': 1, 'total': 3, 'phase': 'return'}
    b = st.buttons()
    assert b.cancel and not b.start and not b.clear
    assert st.headline().title == 'Moving 1 / 3' and 'simulation' in st.headline().subtitle
    st.cancelling = True
    assert not st.buttons().cancel and st.phase() == U.CANCELLING


def test_finished_allows_clear_only():
    st = _ready_state()
    st.recognition = {'ink_id': 4, 'success': True, 'value': 3}
    st.finished = {'outcome': 'canceled', 'message': '已取消', 'ink_id': 4}
    b = st.buttons()
    assert st.phase() == U.FINISHED and not b.start and b.clear and not b.cancel


def test_hardware_connect_button():
    st = _ready_state()
    st.arm = {'mode': 'hardware', 'simulated': False, 'connected': False, 'ready': False}
    b = st.buttons()
    assert b.connect_visible and b.connect
    st.arm['connected'] = True
    assert not st.buttons().connect
