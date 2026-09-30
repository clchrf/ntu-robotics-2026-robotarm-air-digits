"""實測紀錄（WSL）：記錄手勢判斷統計、每次送出的筆跡座標與辨識結果，不存任何影像。

輸出：artifacts/session/<時間>/ 下的 submissions.jsonl（筆跡＋辨識結果）與 gestures.json（手勢統計）。
用法：python3 tools/record_session.py（Ctrl+C 結束）
"""

import collections
import json
import os
import signal
import time

import rclpy
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

from digit_arm_interfaces.msg import HandState, Ink, RecognitionResult

LATCHED = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST,
                     reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'artifacts', 'session',
                   time.strftime('%Y%m%d-%H%M%S'))


def main():
    os.makedirs(OUT, exist_ok=True)
    rclpy.init()
    node = rclpy.create_node('session_recorder')
    stats = collections.Counter()
    transitions = collections.Counter()
    last = {'gesture': None, 'pen': None}
    fist_peaks = []
    inks = {}

    def on_hand(m: HandState):
        g = m.gesture if m.hand_visible else 'none'
        stats[g] += 1
        stats['pen_' + m.pen] += 1
        if last['gesture'] and last['gesture'] != g:
            transitions[f'{last["gesture"]}->{g}'] += 1
        if last['pen'] != m.pen:
            print(f'{time.strftime("%H:%M:%S")} 筆狀態：{last["pen"]} → {m.pen}（手勢 {g}）', flush=True)
        if m.fist_progress > 0:
            if not fist_peaks or fist_peaks[-1][1] == 0:
                fist_peaks.append([m.fist_progress, 1])
            else:
                fist_peaks[-1][0] = max(fist_peaks[-1][0], m.fist_progress)
        elif fist_peaks and fist_peaks[-1][1] == 1:
            fist_peaks[-1][1] = 0
        last['gesture'], last['pen'] = g, m.pen

    def on_submit(m: Ink):
        inks[m.ink_id] = [[list(zip(s.x, s.y))] for s in m.strokes]
        print(f'{time.strftime("%H:%M:%S")} 送出 ink {m.ink_id}：{len(m.strokes)} 筆，'
              f'{sum(len(s.x) for s in m.strokes)} 點', flush=True)

    def on_rec(m: RecognitionResult):
        rec = {'time': time.strftime('%H:%M:%S'), 'ink_id': m.ink_id, 'success': m.success, 'value': m.value,
               'text': m.text, 'confidence': m.confidence, 'reason': m.reason,
               'digits': [{'label': d.label, 'confidence': d.confidence, 'runner_up': d.runner_up,
                           'stroke_indices': list(d.stroke_indices)} for d in m.digits],
               'strokes': [s[0] for s in inks.get(m.ink_id, [])]}
        with open(os.path.join(OUT, 'submissions.jsonl'), 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
        print(f'{rec["time"]} 辨識 ink {m.ink_id}：' + (f'成功 {m.value}（信心 {m.confidence:.2f}）' if m.success
                                                       else f'失敗 — {m.reason}'), flush=True)

    node.create_subscription(HandState, '/digit_arm/hand_state', on_hand, 50)
    node.create_subscription(Ink, '/digit_arm/ink/submitted', on_submit, 10)
    node.create_subscription(RecognitionResult, '/digit_arm/recognition', on_rec, LATCHED)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    print('開始記錄：', os.path.abspath(OUT), flush=True)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        with open(os.path.join(OUT, 'gestures.json'), 'w', encoding='utf-8') as fh:
            json.dump({'frames': dict(stats), 'transitions': dict(transitions.most_common(30)),
                       'fist_progress_peaks': [p[0] for p in fist_peaks]}, fh, ensure_ascii=False, indent=1)
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
