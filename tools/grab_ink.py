"""從正在執行的程式抓下目前的筆跡與辨識結果（latched topic），存成 JSON。不存影像。
用法（WSL，已 source 工作區）：python3 tools/grab_ink.py [輸出檔]"""
import json
import os
import sys
import time

import rclpy
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

from digit_arm_interfaces.msg import Ink, RecognitionResult

LATCHED = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST,
                     reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', 'artifacts',
                                                         f'ink-{time.strftime("%Y%m%d-%H%M%S")}.json')
rclpy.init()
node = rclpy.create_node('grab_ink')
got = {}
node.create_subscription(Ink, '/digit_arm/ink', lambda m: got.update(ink=m), LATCHED)
node.create_subscription(RecognitionResult, '/digit_arm/recognition', lambda m: got.update(rec=m), LATCHED)
end = time.monotonic() + 5
while time.monotonic() < end and not ('ink' in got and 'rec' in got):
    rclpy.spin_once(node, timeout_sec=0.1)
if 'ink' not in got:
    print('沒有收到筆跡：程式有在執行嗎？')
    sys.exit(1)
ink, rec = got['ink'], got.get('rec')
data = {'ink_id': ink.ink_id, 'locked': ink.locked, 'strokes': [list(zip(s.x, s.y)) for s in ink.strokes],
        'recognition': None if rec is None else {'ink_id': rec.ink_id, 'success': rec.success, 'value': rec.value,
                                                 'text': rec.text, 'reason': rec.reason}}
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
json.dump(data, open(out, 'w', encoding='utf-8'), ensure_ascii=False)
print(f'已儲存 {os.path.abspath(out)}：ink {ink.ink_id}，{len(ink.strokes)} 筆，辨識 {data["recognition"]}')
