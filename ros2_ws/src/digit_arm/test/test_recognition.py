"""數字分群與辨識（使用合成手寫；真人空中書寫仍需實測）。"""

import collections

import numpy as np
import pytest

from digit_arm.core import gestures
from digit_arm.core.pen import HandObservation, PenTracker
from digit_arm.core.recognizer import DigitRecognizer, RecognizerConfig
from digit_arm.core.segmentation import group_strokes
from digit_arm.core.synth import write_number
from digit_arm.core.synthetic_hand import writing_sequence
from digit_arm.core.templates import line

REC = DigitRecognizer(RecognizerConfig(max_count=99))


def _stats(texts, n, seed, level=1.0):
    rng = np.random.default_rng(seed)
    c = collections.Counter()
    for text in texts:
        for _ in range(n):
            r = REC.recognize(write_number(text, rng, level))
            c['ok' if r.success and r.text == text else ('wrong' if r.success else 'reject')] += 1
    total = sum(c.values())
    return {k: c[k] / total for k in ('ok', 'wrong', 'reject')}


# ── 分群 ──
def test_group_two_digits_left_to_right():
    one = line((0.5, 0.2), (0.5, 0.5))
    zero = [(0.6 + 0.05 * np.cos(a), 0.35 + 0.15 * np.sin(a)) for a in np.linspace(0, 6.3, 30)]
    groups = group_strokes([zero, one])            # 書寫順序顛倒也沒關係
    assert [g.stroke_indices for g in groups] == [[1], [0]]


@pytest.mark.parametrize('strokes', [
    [line((0.42, 0.0), (0.0, 0.66), (0.6, 0.66)), line((0.42, 0.25), (0.42, 1.0))],   # 兩筆的 4
    [line((0.1, 0.02), (0.08, 0.45), (0.5, 0.7), (0.1, 0.95)), line((0.1, 0.02), (0.55, 0.02))],  # 5 的橫槓
    [line((0.0, 0.02), (0.6, 0.02), (0.2, 1.0)), line((0.18, 0.52), (0.55, 0.52))],   # 7 的橫槓
    [line((0.1, 0.22), (0.3, 0.0)), line((0.3, 0.0), (0.3, 1.0))],                    # 1 的鉤另外寫
])
def test_multi_stroke_digit_stays_together(strokes):
    assert len(group_strokes(strokes)) == 1


# ── 辨識：先驗證 1、2、3、10 ──
# 門檻依大量合成測試的實測（1/2/3/10：正確約 93%、誤判約 1.5%）留一點統計波動空間
@pytest.mark.parametrize('text', ['1', '2', '3', '10'])
def test_priority_numbers(text):
    s = _stats([text], 80, seed=int(text) + 7)
    assert s['ok'] >= 0.8, s
    assert s['wrong'] <= 0.05, s


def test_single_digits_1_to_9():
    s = _stats([str(d) for d in range(1, 10)], 20, seed=11)
    assert s['ok'] >= 0.85, s
    assert s['wrong'] <= 0.03, s


def test_zero_shape_is_recognized_as_digit():
    from digit_arm.core.synth import digit_shape, distort
    rng = np.random.default_rng(8)
    labels = [REC.classify_digit(distort(digit_shape('0', rng), rng)).label for _ in range(20)]
    assert labels.count('0') >= 18


def test_two_digit_numbers():
    rng = np.random.default_rng(3)
    texts = [str(v) for v in rng.integers(10, 100, size=40)]
    s = _stats(texts, 3, seed=12)
    assert s['ok'] >= 0.8, s
    assert s['wrong'] <= 0.05, s


def test_scribbles_are_rejected():
    rng = np.random.default_rng(5)
    rejected = 0
    for _ in range(30):
        pts = np.cumsum(rng.normal(0, 0.05, size=(40, 2)), axis=0) + 0.5
        rejected += not REC.recognize([pts.tolist()]).success
    assert rejected >= 25


def test_overlapping_digits_split_by_writing_order():
    """實測：空中書寫時數字常互相重疊；位置分組失敗時依書寫順序切分。"""
    rng = np.random.default_rng(21)
    ok = wrong = 0
    for _ in range(20):
        strokes = write_number('13', rng, level=0.6)
        # 把 3 往左推，讓它蓋過 1（位置上無法分開）
        one = [s for s in strokes[:1]]
        three = [s.copy() for s in strokes[1:]]
        x1 = max(p[0] for p in one[0])
        shift = min(p[0] for s in three for p in s) - (x1 - 0.03)
        for s in three:
            s[:, 0] -= shift
        r = REC.recognize(one + three)
        ok += r.success and r.value == 13
        wrong += r.success and r.value != 13   # 例如重疊後被看成 8：絕不可接受
    assert wrong == 0, wrong
    assert ok >= 8, ok


def test_order_split_does_not_break_multi_stroke_digits():
    """兩筆的 4、5、7 不應被順序切分成兩個數字。"""
    rng = np.random.default_rng(22)
    multi = 0
    for d in '457':
        for _ in range(25):
            r = REC.recognize(write_number(d, rng))
            multi += r.success and len(r.text) > 1
    assert multi <= 2, multi


# ── 規則 ──
def _num(text, **cfg):
    rec = DigitRecognizer(RecognizerConfig(**cfg))
    rng = np.random.default_rng(1)
    for _ in range(10):
        r = rec.recognize(write_number(text, rng, level=0.5))
        if r.text == text:
            return r
    raise AssertionError('合成筆跡辨識失敗')


def test_rules_leading_zero_zero_and_max():
    r = _num('05', max_count=99)
    assert not r.success and 'cannot start with 0' in r.reason
    r = _num('0', max_count=99)
    assert not r.success and '0' in r.reason
    r = _num('25', max_count=20)
    assert not r.success and r.value == 25 and 'limit is 20' in r.reason
    r = _num('20', max_count=20)
    assert r.success and r.value == 20


def test_empty_tiny_and_too_many_digits():
    assert 'Nothing written' in REC.recognize([]).reason
    assert 'Too small' in REC.recognize([[(0.5, 0.5), (0.505, 0.51)]]).reason
    rng = np.random.default_rng(2)
    r = DigitRecognizer(RecognizerConfig(max_digits=3, max_count=9999)).recognize(write_number('1234', rng, 0.3))
    assert not r.success and 'the most is 3' in r.reason


# ── 整條：合成手勢 → 筆畫 → 辨識 ──
@pytest.mark.parametrize('text', ['1', '2', '3', '10'])
def test_gesture_pipeline(text):
    rng = np.random.default_rng(40 + int(text))
    strokes = write_number(text, rng, level=0.4)
    pen = PenTracker()
    for t, _g, lm in writing_sequence(strokes, rng=rng):
        g = gestures.classify(gestures.to_metric(lm, 640, 480))
        pen.update(t, HandObservation(g, (lm[8][0], lm[8][1])))
    assert pen.locked
    assert len(pen.strokes) == len(strokes)
    r = DigitRecognizer().recognize([[(p[0], p[1]) for p in s] for s in pen.strokes])
    assert r.success and r.value == int(text), r.reason


# ── 真人實測筆跡（只有座標）──
def _real(name):
    import json
    import os
    p = os.path.join(os.path.dirname(__file__), 'data', name)
    d = json.load(open(p, encoding='utf-8'))
    return d['expected'], [[tuple(pt) for pt in s] for s in d['strokes']]


def test_real_32_flat_top_3_with_start_hook():
    expected, strokes = _real('real_32_flat_top_3.json')
    r = DigitRecognizer(RecognizerConfig(max_count=99)).recognize(strokes)
    assert r.success and r.text == expected, (r.text, r.reason)
