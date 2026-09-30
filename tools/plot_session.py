"""把實測紀錄的每次送出畫成圖（artifacts/session/<時間>/ink-<id>.png），並列出辨識細節。
用法：python3 tools/plot_session.py [session 資料夾，預設最新一次]
"""

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'ros2_ws', 'src', 'digit_arm'))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from digit_arm.core.recognizer import DigitRecognizer, left_stem_ratio  # noqa: E402
from digit_arm.core.segmentation import group_strokes  # noqa: E402

COLORS = [(137, 150, 0), (0, 77, 187), (63, 0, 236), (120, 60, 160)]


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob(os.path.join(HERE, '..', 'artifacts', 'session', '*')))[-1]
    rec = DigitRecognizer()
    for line in open(os.path.join(d, 'submissions.jsonl'), encoding='utf-8'):
        r = json.loads(line)
        strokes = [[tuple(p) for p in s] for s in r['strokes']]
        img = np.full((480, 640, 3), 250, np.uint8)
        for i, s in enumerate(strokes):
            pts = np.array([[int(x * 640), int(y * 480)] for x, y in s], np.int32)
            cv2.polylines(img, [pts], False, COLORS[i % len(COLORS)], 3, cv2.LINE_AA)
            cv2.circle(img, tuple(pts[0]), 6, COLORS[i % len(COLORS)], -1)
            cv2.putText(img, f's{i}', tuple(pts[0] + [8, -8]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLORS[i % len(COLORS)], 2)
        for g in group_strokes(strokes):
            cv2.rectangle(img, (int(g.x_min * 640) - 6, int(g.y_min * 480) - 6),
                          (int(g.x_max * 640) + 6, int(g.y_max * 480) + 6), (180, 180, 180), 1)
        out = os.path.join(d, f'ink-{r["ink_id"]}.png')
        cv2.imwrite(out, img)
        again = rec.recognize(strokes)
        print(f'ink {r["ink_id"]}: 當時結果={r["text"] or "失敗"} ({r["reason"]})')
        for k, s in enumerate(strokes):
            xs, ys = [p[0] for p in s], [p[1] for p in s]
            print(f'   s{k}: {len(s)} 點  x {min(xs):.2f}-{max(xs):.2f}  y {min(ys):.2f}-{max(ys):.2f}')
        for dg in again.digits:
            top = sorted(dg.scores, key=dg.scores.get, reverse=True)[:3]
            print(f'   數字 strokes={dg.group.stroke_indices} → {dg.label}  前三名 ' +
                  ', '.join(f'{k}:{dg.scores[k]:.2f}' for k in top) + f'  距離 {dg.distance:.3f}')
        print('   圖：', out)


if __name__ == '__main__':
    main()
