"""從食指筆跡辨識整數。

流程：
1. segmentation.group_strokes 把筆畫分成數字（由左到右）。
2. 每個數字用 $P 點雲辨識器（Vatavu, Anthony & Wobbrock 2012）與 0–9 範本比對。
   $P 與筆畫數、順序、方向無關，適合多筆畫的空中書寫。
3. 各類別最佳距離換成機率（softmax），信心不足、差距太小或形狀離所有範本都太遠就拒絕。
4. 組成整數後檢查位數、開頭 0 與最大動作次數。

任何不確定都回傳 success=False 與原因，呼叫端不得據此啟動手臂。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

from .segmentation import DigitGroup, group_strokes
from .templates import TEMPLATES

N_POINTS = 32
# 合成測試中最常互相誤判的組合
CONFUSABLE = {frozenset(p) for p in [('3', '5'), ('1', '7'), ('4', '9'), ('4', '7'), ('5', '6'), ('0', '6'),
                                     ('1', '2')]}


@dataclass
class RecognizerConfig:
    max_count: int = 20
    max_digits: int = 3
    min_confidence: float = 0.70
    # 容易混淆的數字對（見 CONFUSABLE）需要更高信心，寧可請使用者重寫
    min_confidence_confusable: float = 0.85
    max_distance: float = 0.20     # 每點平均距離上限（正規化後）；超過代表不像任何數字
    temperature: float = 0.010
    min_stroke_points: int = 2
    min_ink_size: float = 0.03     # 整體筆跡外框（正規化影像座標）太小視為雜訊
    min_stroke_ratio: float = 0.10 # 單一筆畫的外框長邊小於整體字高的這個比例 → 視為雜訊點


@dataclass
class DigitResult:
    label: Optional[str]
    confidence: float
    runner_up: str
    distance: float
    group: DigitGroup
    scores: Dict[str, float] = field(default_factory=dict)


@dataclass
class Recognition:
    success: bool
    value: Optional[int]
    text: str
    confidence: float
    reason: str
    digits: List[DigitResult]


# ── $P ─────────────────────────────────────────────────────────
def _resample(strokes: Sequence[Sequence[Sequence[float]]], n: int):
    """沿所有筆畫（筆畫之間不連線）等距取 n 點；同時回傳每點所屬筆畫。"""
    polys = [np.asarray([(p[0], p[1]) for p in s], dtype=float) for s in strokes if len(s) > 0]
    lengths = [float(np.sum(np.linalg.norm(np.diff(p, axis=0), axis=1))) if len(p) > 1 else 0.0 for p in polys]
    total = sum(lengths)
    if total <= 1e-12:
        pts = np.concatenate(polys, axis=0)
        idx = np.linspace(0, len(pts) - 1, n).round().astype(int)
        return pts[idx], np.zeros(n, dtype=int)
    interval = total / (n - 1)
    out = [polys[0][0]]
    ids = [0]
    d_acc = 0.0
    for sid, p in enumerate(polys):
        if len(p) < 2:
            continue
        prev = p[0]
        i = 1
        while i < len(p):
            cur = p[i]
            d = float(np.linalg.norm(cur - prev))
            if d_acc + d >= interval and d > 0:
                q = prev + (interval - d_acc) / d * (cur - prev)
                out.append(q)
                ids.append(sid)
                prev = q
                d_acc = 0.0
            else:
                d_acc += d
                prev = cur
                i += 1
    while len(out) < n:
        out.append(polys[-1][-1])
        ids.append(len(polys) - 1)
    return np.asarray(out[:n]), np.asarray(ids[:n])


def _orientation(pts: np.ndarray, ids: np.ndarray) -> np.ndarray:
    """每點的切線方向，以 (cos2θ, sin2θ) 表示：與書寫方向無關（θ 與 θ+180° 相同）。"""
    n = len(pts)
    feats = np.zeros((n, 2))
    for i in range(n):
        lo = i - 1 if i > 0 and ids[i - 1] == ids[i] else i
        hi = i + 1 if i < n - 1 and ids[i + 1] == ids[i] else i
        v = pts[hi] - pts[lo]
        norm = float(np.hypot(v[0], v[1]))
        if norm < 1e-9:
            continue
        th = math.atan2(v[1], v[0])
        feats[i] = (math.cos(2 * th), math.sin(2 * th))
    return feats


ORIENTATION_WEIGHT = 0.15


def _stroke_extent(stroke) -> float:
    xs = [p[0] for p in stroke]
    ys = [p[1] for p in stroke]
    return max(max(xs) - min(xs), max(ys) - min(ys))


def left_stem_ratio(strokes) -> float:
    """左上區域（左 40%、高度 8%～50%）中接近垂直的點所占比例。用來分辨 3 與 5。

    每一筆開頭與結尾各 10% 不計：空中書寫起筆、收筆常有小勾（實測：頂部平的 3 起筆往上勾，
    被當成 5 的豎筆），5 的豎筆則在筆畫中段或佔一段足夠長的距離。
    """
    pts, ids = _resample(strokes, 64)
    mn, mx = pts.min(axis=0), pts.max(axis=0)
    q = (pts - mn) / np.maximum(mx - mn, 1e-6)
    o = _orientation(pts, ids)
    frac = np.zeros(len(pts))
    for sid in np.unique(ids):
        idx = np.where(ids == sid)[0]
        seg = np.linalg.norm(np.diff(pts[idx], axis=0), axis=1)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        frac[idx] = cum / max(cum[-1], 1e-9)
    inner = (frac >= 0.1) & (frac <= 0.9)
    region = (q[:, 0] < 0.4) & (q[:, 1] > 0.08) & (q[:, 1] < 0.5)
    return float(np.mean(region & inner & (o[:, 0] < -0.5)))


def normalize(strokes, orientation_weight: float = ORIENTATION_WEIGHT) -> np.ndarray:
    """回傳 (N, 4)：置中、等比例縮放後的位置，加上加權的切線方向特徵。"""
    pts, ids = _resample(strokes, N_POINTS)
    mn = pts.min(axis=0)
    mx = pts.max(axis=0)
    scale = float(max(mx[0] - mn[0], mx[1] - mn[1]))
    if scale < 1e-9:
        scale = 1.0
    pts = (pts - mn) / scale
    pts = pts - pts.mean(axis=0)
    return np.hstack([pts, orientation_weight * _orientation(pts, ids)])


def _cloud_distance(a: np.ndarray, b: np.ndarray, start: int, dmat: np.ndarray) -> float:
    n = len(a)
    matched = np.zeros(n, dtype=bool)
    total = 0.0
    weight_step = 1.0 / n
    for k in range(n):
        i = (start + k) % n
        row = np.where(matched, np.inf, dmat[i])
        j = int(np.argmin(row))
        matched[j] = True
        total += (1.0 - k * weight_step) * row[j]
    return total


def greedy_cloud_match(a: np.ndarray, b: np.ndarray) -> float:
    n = len(a)
    step = max(1, int(math.floor(n ** 0.5)))
    d_ab = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    d_ba = d_ab.T
    best = math.inf
    for start in range(0, n, step):
        best = min(best, _cloud_distance(a, b, start, d_ab), _cloud_distance(b, a, start, d_ba))
    # 換成「每點平均距離」，方便設定門檻
    return best / (n * 0.5 + 0.5)


class DigitRecognizer:
    def __init__(self, cfg: RecognizerConfig | None = None, templates=None,
                 orientation_weight: float = ORIENTATION_WEIGHT):
        self.cfg = cfg or RecognizerConfig()
        self._ow = orientation_weight
        src = templates or TEMPLATES
        self._templates = [(label, normalize(strokes, self._ow))
                           for label, variants in src.items() for strokes in variants]

    def classify_digit(self, strokes) -> DigitResult:
        cloud = normalize(strokes, self._ow)
        per_class: Dict[str, float] = {}
        for label, tpl in self._templates:
            d = greedy_cloud_match(cloud, tpl)
            if d < per_class.get(label, math.inf):
                per_class[label] = d
        labels = sorted(per_class, key=per_class.get)
        dists = np.array([per_class[k] for k in labels])
        logits = -(dists - dists[0]) / self.cfg.temperature
        probs = np.exp(logits) / np.sum(np.exp(logits))
        scores = {k: float(p) for k, p in zip(labels, probs)}
        best, second = labels[0], labels[1]
        conf = scores[best]
        need = self.cfg.min_confidence
        pair = frozenset((best, second))
        if pair == frozenset(('3', '5')):
            # $P 只能確定「3 或 5」時，由結構特徵決定：5 的左上方有一段接近垂直的直筆，3 沒有
            # （合成資料：3 的 95% ≤ 0.047，5 的 95% ≥ 0.062）
            f = left_stem_ratio(strokes)
            says = '3' if f < 0.03 else ('5' if f > 0.08 else None)
            if says is None:
                need = max(need, self.cfg.min_confidence_confusable)
            else:
                best, second = says, ('5' if says == '3' else '3')
                conf = scores['3'] + scores['5']
                need = max(need, self.cfg.min_confidence_confusable)
        elif pair in CONFUSABLE:
            need = max(need, self.cfg.min_confidence_confusable)
        ok = conf >= need and per_class[best] <= self.cfg.max_distance
        scores = dict(scores)
        scores[best] = max(scores[best], conf) if ok else scores[best]
        return DigitResult(best if ok else None, conf, second, per_class[best],
                           DigitGroup([], 0, 0, 0, 0), scores)

    def _split_by_order(self, strokes):
        """把「依書寫順序連續」的筆畫切成數個數字。

        回傳 (最佳切法, 所有合理切法讀出的文字集合)；沒有合理切法時為 (None, set())。

        人寫多位數時幾乎都是一個數字寫完才寫下一個；數字在空中寫得互相重疊時，
        位置分組會把它們併在一起，這時用順序切分。條件：
        - 每一段都要被有信心地辨識（與一般辨識相同的門檻）
        - 後一段的中心接近或超過前一段的右緣，且第一筆不在前一段的左緣左邊
        - 第一段不是 0
        """
        n = len(strokes)
        if n > 8:
            return None, set()
        cache = {}

        def seg(a: int, b: int) -> DigitResult:
            if (a, b) not in cache:
                part = strokes[a:b]
                res = self.classify_digit(part)
                xs = [p[0] for s in part for p in s]
                ys = [p[1] for s in part for p in s]
                res.group = DigitGroup(list(range(a, b)), min(xs), max(xs), min(ys), max(ys))
                cache[(a, b)] = res
            return cache[(a, b)]

        best, best_score = None, -math.inf
        texts = set()
        for mask in range(1, 1 << (n - 1)):  # 至少切一刀；不切＝整份筆跡當一個數字，前面已試過
            cuts = [k + 1 for k in range(n - 1) if mask >> k & 1]
            bounds = [0] + cuts + [n]
            if len(bounds) - 1 > self.cfg.max_digits:
                continue
            parts = [seg(a, b) for a, b in zip(bounds, bounds[1:])]
            if any(r.label is None for r in parts) or parts[0].label == '0':
                continue  # 多位數不會以 0 開頭（避免把 8 拆成 00、9 拆成 01）
            ok = True
            for prev, cur, a in zip(parts, parts[1:], bounds[1:-1]):
                start_x = strokes[a][0][0]
                # 下一個數字的中心要接近或超過前一個數字的右緣（4 的直線、7 的橫槓都在字的範圍內）
                if (cur.group.cx < prev.group.x_max - 0.15 * prev.group.width
                        or start_x <= prev.group.x_min):
                    ok = False
                    break
            if not ok:
                continue
            texts.add(''.join(r.label for r in parts))
            score = sum(math.log(max(r.confidence, 1e-6)) for r in parts)
            if score > best_score:
                best, best_score = parts, score
        return best, texts

    def recognize(self, strokes: Sequence[Sequence[Sequence[float]]]) -> Recognition:
        cfg = self.cfg
        strokes = [s for s in strokes if len(s) >= cfg.min_stroke_points]
        if not strokes:
            return Recognition(False, None, '', 0.0, 'Nothing written. Point your index finger to write.', [])
        xs = [p[0] for s in strokes for p in s]
        ys = [p[1] for s in strokes for p in s]
        if max(max(xs) - min(xs), max(ys) - min(ys)) < cfg.min_ink_size:
            return Recognition(False, None, '', 0.0, 'Too small. Please write bigger.', [])

        # 忽略換手勢時不小心留下的小點（實測：3 個點、只有字高 4% 的筆畫）
        height = max(ys) - min(ys)
        kept = [s for s in strokes if _stroke_extent(s) >= cfg.min_stroke_ratio * height]
        if kept and len(kept) < len(strokes):
            strokes = kept

        groups = group_strokes(strokes)
        digits: List[DigitResult] = []
        if len(groups) <= cfg.max_digits:
            for g in groups:
                res = self.classify_digit([strokes[i] for i in g.stroke_indices])
                res.group = g
                digits.append(res)

        # 數字在空中容易寫得互相重疊。只要位置分組把多個筆畫併成一個數字，就再依書寫順序切分比對：
        # - 位置分組失敗、順序切分成功 → 採用順序切分
        # - 兩者都成功，但任何一種合理的順序切法讀出不同數字（例如 1 疊在 3 上像 8；
        #   2 旁邊多一個小圈像 4）→ 不猜，請使用者拉開間距重寫
        merged = not digits or any(len(d.group.stroke_indices) > 1 for d in digits)
        if merged and len(strokes) >= 2:
            ordered, texts = self._split_by_order(strokes)
            spatial_ok = bool(digits) and all(d.label is not None for d in digits)
            if ordered is not None:
                if not spatial_ok:
                    digits = ordered
                else:
                    a = ''.join(d.label for d in digits)
                    others = sorted(t for t in texts if t != a)
                    if others:
                        alt = '" or "'.join(others[:2])
                        return Recognition(False, None, '', 0.0,
                                           f'Not sure if this is "{a}" or "{alt}". Leave more space between digits, '
                                           'and make a fist right after writing.', digits)

        if not digits:
            return Recognition(False, None, '', 0.0,
                               f'Found {len(groups)} digits, but the most is {cfg.max_digits}. '
                               'If it is one digit, keep its strokes closer.', [])

        unsure = [i for i, d in enumerate(digits) if d.label is None]
        if unsure:
            i = unsure[0]
            d = digits[i]
            best = max(d.scores, key=d.scores.get)
            pos = f'digit {i + 1}' if len(digits) > 1 else 'this digit'
            reason = (f'Not sure about {pos} (looks like {best} or {d.runner_up}). Please clear and rewrite.'
                      if d.distance <= cfg.max_distance else
                      f'Cannot read {pos}. Please clear and write it more clearly.')
            return Recognition(False, None, '', min(x.confidence for x in digits), reason, digits)

        text = ''.join(d.label for d in digits)
        conf = float(min(d.confidence for d in digits))
        if len(text) > 1 and text[0] == '0':
            return Recognition(False, None, text, conf, f'Looks like "{text}". A number cannot start with 0. Please rewrite.', digits)
        value = int(text)
        if value == 0:
            return Recognition(False, 0, text, conf, 'You wrote 0. Please write 1 or more.', digits)
        if value > cfg.max_count:
            return Recognition(False, value, text, conf,
                               f'You wrote {value}, but the limit is {cfg.max_count}. Please rewrite.', digits)
        return Recognition(True, value, text, conf, '', digits)
