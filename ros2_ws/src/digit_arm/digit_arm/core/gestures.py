"""由 MediaPipe 21 個手部關鍵點判斷手勢。

只看「手指伸直或彎曲」，不數手指當數字：
  point = 只有食指伸直          → 下筆
  open  = 至少三指伸直（含中指）→ 抬筆
  fist  = 四指都彎曲            → 握拳（維持一段時間才送出，由 pen.PenTracker 判斷）
其餘姿勢一律 other（視為抬筆）。

判斷使用距離比例與關節彎曲角度，與手的大小、位置、旋轉無關。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

WRIST = 0
INDEX_TIP = 8
# (MCP, PIP, DIP, TIP)
FINGERS = {
    'index': (5, 6, 7, 8),
    'middle': (9, 10, 11, 12),
    'ring': (13, 14, 15, 16),
    'pinky': (17, 18, 19, 20),
}

POINT = 'point'
OPEN = 'open'
FIST = 'fist'
OTHER = 'other'
NONE = 'none'

EXTENDED = 'ext'
FOLDED = 'fold'
BENT = 'mid'


@dataclass(frozen=True)
class GestureConfig:
    # 指尖到手腕的距離 / PIP 到手腕的距離
    extended_ratio: float = 1.18
    folded_ratio: float = 1.02
    # PIP→TIP 與 MCP→PIP 方向夾角的餘弦（1 = 完全伸直）
    straight_cos: float = 0.55


Point3 = Sequence[float]


def _dist(a: Point3, b: Point3) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(len(a))))


def _straightness(mcp: Point3, pip: Point3, tip: Point3) -> float:
    v1 = [pip[i] - mcp[i] for i in range(len(mcp))]
    v2 = [tip[i] - pip[i] for i in range(len(mcp))]
    n1 = math.sqrt(sum(c * c for c in v1))
    n2 = math.sqrt(sum(c * c for c in v2))
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    return sum(v1[i] * v2[i] for i in range(len(v1))) / (n1 * n2)


def finger_states(landmarks: Sequence[Point3], cfg: GestureConfig = GestureConfig()) -> dict:
    """回傳每根手指（不含拇指）的 ext / mid / fold。

    landmarks 為 21 個點，座標需先換成等比例單位（例如 x*寬, y*高, z*寬）。
    """
    wrist = landmarks[WRIST]
    states = {}
    for name, (mcp_i, pip_i, _dip_i, tip_i) in FINGERS.items():
        mcp, pip, tip = landmarks[mcp_i], landmarks[pip_i], landmarks[tip_i]
        d_pip = _dist(wrist, pip)
        ratio = _dist(wrist, tip) / d_pip if d_pip > 1e-9 else 0.0
        straight = _straightness(mcp, pip, tip)
        if ratio >= cfg.extended_ratio and straight >= cfg.straight_cos:
            states[name] = EXTENDED
        elif ratio <= cfg.folded_ratio:
            states[name] = FOLDED
        else:
            states[name] = BENT
    return states


def classify(landmarks: Sequence[Point3] | None, cfg: GestureConfig = GestureConfig()) -> str:
    if not landmarks or len(landmarks) < 21:
        return NONE
    s = finger_states(landmarks, cfg)
    others = [s['middle'], s['ring'], s['pinky']]
    if s['index'] == EXTENDED and EXTENDED not in others and others.count(FOLDED) >= 2:
        return POINT
    extended = [k for k, v in s.items() if v == EXTENDED]
    if len(extended) >= 3 and s['middle'] == EXTENDED:
        return OPEN
    if all(v == FOLDED for v in s.values()):
        return FIST
    return OTHER


def to_metric(landmarks_norm: Sequence[Point3], width: int, height: int) -> list:
    """把 MediaPipe 正規化座標轉成等比例單位（z 與 x 同尺度）。"""
    out = []
    for p in landmarks_norm:
        z = p[2] if len(p) > 2 else 0.0
        out.append((p[0] * width, p[1] * height, z * width))
    return out
