"""合成「空中手寫」筆跡，只用於測試與合成示範（不是真人資料）。

字形由隨機參數產生（和 templates.py 的固定範本不同），再加上
旋轉、傾斜、低頻扭曲、手抖雜訊、起筆／收筆小鉤、筆畫順序與方向隨機、偶發斷筆，
模擬食指在空中書寫、經 One Euro 濾波後的樣子。
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import numpy as np

from .templates import arc, line

Pt = Tuple[float, float]


def _u(rng, a, b):
    return float(rng.uniform(a, b))


def digit_shape(d: str, rng: np.random.Generator) -> List[List[Pt]]:
    u = lambda a, b: _u(rng, a, b)  # noqa: E731
    p = rng.random
    if d == '0':
        start = u(230, 310)
        sign = 1 if p() < 0.5 else -1
        return [arc(0.3, 0.5, u(0.17, 0.31), u(0.42, 0.5), start, start + sign * (360 + u(-15, 30)), 40)]
    if d == '1':
        top_x = 0.3 + u(-0.06, 0.06)
        bot_x = top_x + u(-0.1, 0.06)
        main = [(top_x, u(0.0, 0.04)), (bot_x, u(0.96, 1.0))]
        if p() < 0.45:
            main = [(top_x - u(0.1, 0.22), u(0.14, 0.3))] + main
        strokes = [main]
        if p() < 0.12:
            strokes.append([(bot_x - u(0.12, 0.2), 1.0), (bot_x + u(0.12, 0.2), 1.0)])
        return strokes
    if d == '2':
        cy = u(0.22, 0.32)
        r = u(0.22, 0.3)
        top = arc(0.3, cy, r, u(0.2, 0.3), u(170, 215), u(365, 420), 20)
        return [top + [(u(0.0, 0.1), u(0.92, 1.0)), (u(0.5, 0.66), u(0.94, 1.02))]]
    if d == '3':
        if p() < 0.25:
            upper = [(u(0.0, 0.1), u(0.0, 0.05)), (u(0.5, 0.6), u(0.0, 0.05)), (u(0.2, 0.3), u(0.38, 0.46))]
            lower = arc(0.3, u(0.68, 0.75), u(0.24, 0.32), u(0.24, 0.3), u(240, 265), u(490, 540), 22)
            return [upper + lower]
        mid = u(0.46, 0.54)
        ru = u(0.2, 0.27)
        upper = arc(0.3, mid - ru, u(0.2, 0.28), ru, u(190, 235), 450, 20)
        rl = 1.0 - mid
        lower = arc(0.3, mid + rl / 2 + 0.0, u(0.24, 0.33), rl / 2 + u(-0.02, 0.02), 270, u(490, 540), 22)
        # 讓下半部從中點開始
        lower = [(x, y - (lower[0][1] - upper[-1][1])) for x, y in lower]
        return [upper + lower]
    if d == '4':
        v = p()
        vx = u(0.38, 0.5)
        ybar = u(0.55, 0.7)
        if v < 0.45:
            return [[(vx + u(-0.05, 0.03), 0.0), (u(0.0, 0.08), ybar), (u(0.55, 0.66), ybar + u(-0.03, 0.03))],
                    [(vx, u(0.1, 0.35)), (vx + u(-0.03, 0.03), 1.0)]]
        if v < 0.8:
            return [[(u(0.02, 0.1), 0.0), (u(0.02, 0.1), ybar), (u(0.55, 0.66), ybar)],
                    [(vx, u(0.0, 0.1)), (vx + u(-0.03, 0.03), 1.0)]]
        return [[(vx, 1.0), (vx, 0.0), (u(0.0, 0.08), ybar), (u(0.55, 0.66), ybar)]]
    if d == '5':
        top_y = u(0.0, 0.05)
        corner = (u(0.06, 0.14), top_y)
        neck = (u(0.04, 0.12), u(0.4, 0.5))
        belly = arc(0.28, u(0.68, 0.74), u(0.25, 0.32), u(0.24, 0.28), u(230, 250), u(495, 530), 24)
        bar = [(corner[0], top_y), (u(0.5, 0.62), top_y + u(-0.03, 0.03))]
        if p() < 0.5:
            return [[corner, neck] + belly, bar]
        return [list(reversed(bar)) + [neck] + belly]
    if d == '6':
        loop_r = u(0.22, 0.29)
        cy = 1.0 - loop_r - u(0.0, 0.03)
        stem = [(u(0.42, 0.55), 0.0), (u(0.2, 0.32), u(0.1, 0.2)), (u(0.06, 0.16), u(0.35, 0.45)), (0.3 - loop_r, cy)]
        return [stem + arc(0.3, cy, loop_r, loop_r * u(0.9, 1.05), 180, u(500, 540), 28)]
    if d == '7':
        tip = (u(0.0, 0.05), u(0.0, 0.05))
        corner = (u(0.55, 0.65), u(0.0, 0.05))
        foot = (u(0.12, 0.35), u(0.96, 1.0))
        strokes = [[tip, corner, foot]]
        if p() < 0.3:
            strokes[0] = [(tip[0], tip[1] + u(0.1, 0.18))] + strokes[0]
        if p() < 0.3:
            strokes.append([(u(0.18, 0.3), 0.52), (u(0.5, 0.6), 0.5)])
        return strokes
    if d == '8':
        split = u(0.44, 0.54)
        top = arc(0.3, split / 2, u(0.16, 0.24), split / 2, 0, 360, 28)
        bot = arc(0.3, split + (1 - split) / 2, u(0.22, 0.3), (1 - split) / 2, 0, 360, 28)
        if p() < 0.5:
            return [top, bot]
        # 單筆 8：上圈接下圈
        top = arc(0.3, split / 2, u(0.16, 0.24), split / 2, 90, 450, 28)
        bot = arc(0.3, split + (1 - split) / 2, u(0.22, 0.3), (1 - split) / 2, 270, -90, 28)
        return [top + bot]
    if d == '9':
        r = u(0.22, 0.28)
        cy = u(0.24, 0.32)
        loop = arc(0.3, cy, r, u(0.22, 0.3), u(-10, 20), u(350, 380), 30)
        tail_x = 0.3 + r
        if p() < 0.5:
            return [loop + [(tail_x + u(-0.04, 0.02), 1.0)]]
        if p() < 0.5:
            return [loop, [(tail_x, cy - u(0.1, 0.2)), (tail_x + u(-0.04, 0.02), 1.0)]]
        return [loop + [(tail_x - 0.02, 0.7), (u(0.2, 0.35), 1.0)]]
    raise ValueError(d)


def _densify(stroke: Sequence[Pt], step: float = 0.01) -> np.ndarray:
    pts = np.asarray(stroke, dtype=float)
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(math.ceil(np.linalg.norm(b - a) / step)))
        for i in range(1, n + 1):
            out.append(a + (b - a) * i / n)
    return np.asarray(out)


def distort(strokes: List[List[Pt]], rng: np.random.Generator, level: float = 1.0) -> List[np.ndarray]:
    ang = math.radians(rng.uniform(-12, 12) * level)
    shear = rng.uniform(-0.2, 0.2) * level
    sx = rng.uniform(0.85, 1.2)
    m = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]]) @ np.array([[sx, shear], [0, 1]])
    phase = rng.uniform(0, 2 * math.pi, size=4)
    amp = 0.035 * level

    out = []
    order = list(range(len(strokes)))
    if rng.random() < 0.3:
        rng.shuffle(order)
    for idx in order:
        s = _densify(strokes[idx])
        if rng.random() < 0.3:
            s = s[::-1]
        s = s @ m.T
        warp = np.stack([
            amp * np.sin(2.1 * s[:, 1] * math.pi + phase[0]) + amp * 0.5 * np.sin(3.3 * s[:, 0] * math.pi + phase[1]),
            amp * np.sin(1.7 * s[:, 0] * math.pi + phase[2]) + amp * 0.5 * np.sin(2.9 * s[:, 1] * math.pi + phase[3]),
        ], axis=1)
        s = s + warp
        s = s + rng.normal(0, 0.008 * level, size=s.shape)
        k = 3
        if len(s) > k:
            kernel = np.ones(k) / k
            s = np.stack([np.convolve(s[:, 0], kernel, mode='same'), np.convolve(s[:, 1], kernel, mode='same')], axis=1)
            s[0], s[-1] = s[1], s[-2]
        for end in (0, -1):
            if rng.random() < 0.25 * level:
                d = rng.normal(0, 1, 2)
                d = d / (np.linalg.norm(d) + 1e-9) * rng.uniform(0.02, 0.06)
                hook = s[end] + np.linspace(0, 1, 4)[1:, None] * d
                s = np.vstack([hook[::-1], s]) if end == 0 else np.vstack([s, hook])
        keep = rng.random(len(s)) > 0.25
        keep[0] = keep[-1] = True
        s = s[keep]
        if rng.random() < 0.08 * level and len(s) > 20:
            cut = int(rng.integers(8, len(s) - 8))
            out.append(s[:cut])
            out.append(s[cut + 2:])
        else:
            out.append(s)
    return out


def write_number(text: str, rng: np.random.Generator, level: float = 1.0,
                 origin: Tuple[float, float] = (0.2, 0.3), height: float = 0.32) -> List[np.ndarray]:
    """回傳正規化影像座標（x 右、y 下）的筆畫。數字之間留隨機空隙。"""
    strokes: List[np.ndarray] = []
    x = origin[0]
    for ch in text:
        h = height * rng.uniform(0.88, 1.12)
        ds = distort(digit_shape(ch, rng), rng, level)
        allp = np.vstack(ds)
        mn, mx = allp.min(axis=0), allp.max(axis=0)
        scale = h / max(mx[1] - mn[1], 1e-6)
        dy = rng.uniform(-0.05, 0.05) * height
        for s in ds:
            q = (s - mn) * scale
            q[:, 0] += x
            q[:, 1] += origin[1] + dy
            strokes.append(q)
        width = (mx[0] - mn[0]) * scale
        x += width + height * rng.uniform(0.22, 0.5)
    return strokes
