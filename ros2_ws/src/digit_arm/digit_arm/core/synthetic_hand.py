"""合成 MediaPipe 格式的 21 個手部關鍵點（僅供測試與「合成示範」）。

以簡化的 3D 手掌模型產生伸直／彎曲的手指，讓 gestures.classify 可以
在沒有鏡頭的情況下測試；並可把一段數字筆跡轉成「食指書寫 → 張開手掌換筆 →
握拳送出」的逐格序列。
"""

from __future__ import annotations

import math
from typing import Iterator, List, Optional, Sequence, Tuple

import numpy as np

from . import gestures

# 手掌局部座標（手指朝上 = -y，+z 朝向鏡頭），單位約為手掌長
_MCP = {'index': (-0.28, -0.95), 'middle': (-0.05, -1.0), 'ring': (0.17, -0.95), 'pinky': (0.36, -0.83)}
_SEG = {'index': (0.45, 0.28, 0.22), 'middle': (0.50, 0.30, 0.24),
        'ring': (0.46, 0.28, 0.22), 'pinky': (0.36, 0.22, 0.20)}
_ORDER = ['index', 'middle', 'ring', 'pinky']
_CURL = {'ext': (5.0, 5.0, 5.0), 'fold': (80.0, 95.0, 70.0), 'mid': (40.0, 45.0, 30.0)}


def _finger(mcp_xy, lengths, curl_deg, spread_deg):
    base_dir = np.array([math.sin(math.radians(spread_deg)), -math.cos(math.radians(spread_deg)), 0.0])
    normal = np.array([0.0, 0.0, 1.0])
    p = np.array([mcp_xy[0], mcp_xy[1], 0.0])
    pts = [p.copy()]
    ang = 0.0
    for seg, c in zip(lengths, curl_deg):
        ang += math.radians(c)
        d = math.cos(ang) * base_dir + math.sin(ang) * normal
        p = p + seg * d
        pts.append(p.copy())
    return pts  # MCP, PIP, DIP, TIP


def hand_landmarks(gesture: str, tip_xy: Tuple[float, float], size: float = 0.13,
                   roll_deg: float = 0.0, image_wh: Tuple[int, int] = (640, 480),
                   rng: Optional[np.random.Generator] = None, noise: float = 0.0) -> List[List[float]]:
    """回傳 21 個 [x, y, z]（正規化影像座標），食指指尖位於 tip_xy。"""
    states = {
        gestures.POINT: {'index': 'ext', 'middle': 'fold', 'ring': 'fold', 'pinky': 'fold'},
        gestures.OPEN: {'index': 'ext', 'middle': 'ext', 'ring': 'ext', 'pinky': 'ext'},
        gestures.FIST: {'index': 'fold', 'middle': 'fold', 'ring': 'fold', 'pinky': 'fold'},
        gestures.OTHER: {'index': 'mid', 'middle': 'mid', 'ring': 'fold', 'pinky': 'ext'},
    }[gesture]
    pts = [None] * 21
    pts[0] = np.zeros(3)
    thumb_curl = 'fold' if gesture in (gestures.FIST, gestures.POINT) else 'ext'
    thumb = [np.array(v) for v in ([-0.3, -0.25, 0.0], [-0.5, -0.45, 0.05])]
    if thumb_curl == 'ext':
        thumb += [np.array([-0.68, -0.62, 0.05]), np.array([-0.82, -0.8, 0.05])]
    else:
        thumb += [np.array([-0.4, -0.65, 0.2]), np.array([-0.22, -0.7, 0.25])]
    for i, p in enumerate(thumb):
        pts[1 + i] = p
    for k, name in enumerate(_ORDER):
        spread = [-8, -2, 4, 10][k] if states[name] != 'fold' else 0
        f = _finger(_MCP[name], _SEG[name], _CURL[states[name]], spread)
        base = 5 + 4 * k
        for j, p in enumerate(f):
            pts[base + j] = p
    arr = np.array(pts)
    rot = math.radians(roll_deg)
    rm = np.array([[math.cos(rot), -math.sin(rot), 0], [math.sin(rot), math.cos(rot), 0], [0, 0, 1]])
    arr = arr @ rm.T
    w, h = image_wh
    px = size * h  # 手掌長（像素）
    arr = arr * px
    if rng is not None and noise > 0:
        arr = arr + rng.normal(0, noise * px, arr.shape)
    anchor = arr[gestures.INDEX_TIP].copy()
    out = []
    for p in arr:
        out.append([tip_xy[0] + (p[0] - anchor[0]) / w,
                    tip_xy[1] + (p[1] - anchor[1]) / h,
                    (p[2] - anchor[2]) / w])
    return out


def _path(points: Sequence[Sequence[float]], speed: float, fps: float) -> Iterator[Tuple[float, float]]:
    pts = np.asarray(points, dtype=float)
    step = speed / fps
    yield float(pts[0][0]), float(pts[0][1])
    carry = 0.0
    for a, b in zip(pts, pts[1:]):
        seg = float(np.linalg.norm(b - a))
        pos = step - carry
        while pos <= seg:
            q = a + (b - a) * (pos / seg)
            yield float(q[0]), float(q[1])
            pos += step
        carry = seg - (pos - step)
    yield float(pts[-1][0]), float(pts[-1][1])


def writing_sequence(strokes: Sequence[Sequence[Sequence[float]]], fps: float = 30.0,
                     speed: float = 0.45, fist_s: float = 1.2,
                     rng: Optional[np.random.Generator] = None) -> List[Tuple[float, str, List[List[float]]]]:
    """(時間, 手勢, 關鍵點) 序列：寫完每一筆 → 張開手掌移到下一筆 → 最後握拳。"""
    rng = rng or np.random.default_rng(0)
    frames = []
    t = 0.0
    dt = 1.0 / fps

    def add(g, xy, count=1):
        nonlocal t
        for _ in range(count):
            frames.append((t, g, hand_landmarks(g, xy, rng=rng, noise=0.004)))
            t += dt

    prev = None
    for s in strokes:
        start = (float(s[0][0]), float(s[0][1]))
        if prev is not None:
            for xy in _path([prev, start], speed * 1.5, fps):
                add(gestures.OPEN, xy)
        add(gestures.OTHER, start, 2)
        add(gestures.POINT, start, 4)
        last = start
        for xy in _path(s, speed, fps):
            add(gestures.POINT, xy)
            last = xy
        add(gestures.POINT, last, 2)
        add(gestures.OTHER, last, 2)
        add(gestures.OPEN, last, 5)
        prev = last
    add(gestures.OTHER, prev or (0.5, 0.5), 2)
    add(gestures.FIST, prev or (0.5, 0.5), int(fist_s * fps))
    add(gestures.OPEN, prev or (0.5, 0.5), 10)
    return frames
