"""把多個筆畫分成一個個數字。

規則（與書寫順序無關）：
- 筆畫之間一定以「抬筆」（張開手掌）分開，所以每個筆畫只屬於一個數字。
- 兩個筆畫群組只要「其中一方的水平中心落在另一方的水平範圍內」（範圍左右各放寬
  margin_ratio × 整體字高），就視為同一個數字；例如 4 的直線、5 與 7 的橫槓。
- 不符合就是不同數字。所以兩個數字之間要留一點水平空隙，
  數字由左到右排列即得到位數順序。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

Stroke = Sequence[Sequence[float]]  # [(x, y, ...), ...]


@dataclass
class DigitGroup:
    stroke_indices: List[int]
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    @property
    def cx(self) -> float:
        return (self.x_min + self.x_max) / 2

    @property
    def width(self) -> float:
        return self.x_max - self.x_min


def _bbox(stroke: Stroke):
    xs = [p[0] for p in stroke]
    ys = [p[1] for p in stroke]
    return min(xs), max(xs), min(ys), max(ys)


def _merge(a: DigitGroup, b: DigitGroup) -> DigitGroup:
    return DigitGroup(sorted(a.stroke_indices + b.stroke_indices),
                      min(a.x_min, b.x_min), max(a.x_max, b.x_max),
                      min(a.y_min, b.y_min), max(a.y_max, b.y_max))


def group_strokes(strokes: Sequence[Stroke], margin_ratio: float = 0.06) -> List[DigitGroup]:
    groups = []
    for i, s in enumerate(strokes):
        if len(s) == 0:
            continue
        x0, x1, y0, y1 = _bbox(s)
        groups.append(DigitGroup([i], x0, x1, y0, y1))
    if not groups:
        return []
    height = max(g.y_max for g in groups) - min(g.y_min for g in groups)
    margin = margin_ratio * max(height, 1e-6)

    def same_digit(a: DigitGroup, b: DigitGroup) -> bool:
        return (a.x_min - margin <= b.cx <= a.x_max + margin) or \
               (b.x_min - margin <= a.cx <= b.x_max + margin)

    changed = True
    while changed:
        changed = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if same_digit(groups[i], groups[j]):
                    merged = _merge(groups[i], groups[j])
                    groups = [g for k, g in enumerate(groups) if k not in (i, j)] + [merged]
                    changed = True
                    break
            if changed:
                break
    groups.sort(key=lambda g: g.cx)
    return groups
