"""自繪元件：鏡頭預覽（含手部與筆跡疊圖）、筆跡卡、手勢提示、進度條、狀態膠囊。"""

from __future__ import annotations

from typing import List, Optional, Sequence

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..core.segmentation import group_strokes
from . import style as S

HAND_EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
              (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]
# 建議書寫範圍（鏡像後正規化座標 x0, y0, x1, y1）：下緣留空，寫字時手掌才不會出界
WRITING_ZONE = (0.10, 0.06, 0.90, 0.62)
# 淺色紙面板上的數字分組顏色（對應 Easy／Norm／Hard 的青綠、琥珀、玫瑰）
DIGIT_COLORS = ['#0f766e', '#b45309', '#be123c']


class Card(QFrame):
    def __init__(self, title: str = '', parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 16, 20, 18)
        self.body.setSpacing(8)
        if title:
            lab = QLabel(title)
            lab.setObjectName('section')
            self.body.addWidget(lab)


class Pill(QLabel):
    def __init__(self, text: str = '', parent=None):
        super().__init__(text, parent)
        self.setObjectName('pill')
        self.set_tone('neutral')

    def set_tone(self, tone: str, text: Optional[str] = None):
        # 類似 BETA 徽章：半透明深底、細框、等寬字
        colors = {'neutral': (S.ELEVATED, S.SECONDARY, S.BORDER),
                  'ok': ('#f0fdfa', S.TEAL, '#96f7e4'),
                  'warn': ('#fffbeb', S.YELLOW, '#fee685'),
                  'error': ('#fff1f2', S.ROSE, '#ffccd3'),
                  'active': (S.ELEVATED, S.STRONG, S.BORDER_STRONG)}
        bg, fg, border = colors.get(tone, colors['neutral'])
        self.setStyleSheet(f'QLabel#pill {{ background: {bg}; color: {fg}; border: 1px solid {border}; }}')
        if text is not None:
            self.setText('● ' + text)


def draw_gesture_icon(p: QPainter, r: QRectF, kind: str, color: QColor, cut: Optional[QColor] = None):
    """簡單的向量手勢圖示（系統沒有 emoji 字型時也能顯示）。"""
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    w, h = r.width(), r.height()
    x0, y0 = r.left(), r.top()
    palm = QRectF(x0 + w * 0.22, y0 + h * 0.46, w * 0.56, h * 0.44)
    fw = w * 0.13
    if kind == 'fist':
        body = QRectF(x0 + w * 0.18, y0 + h * 0.3, w * 0.64, h * 0.56)
        p.drawRoundedRect(body, w * 0.18, w * 0.18)
        p.setPen(QPen(cut or S.qc(S.CARD), max(1.5, w * 0.05), cap=Qt.PenCapStyle.RoundCap))
        for i in range(1, 4):
            x = body.left() + body.width() * i / 4
            p.drawLine(QPointF(x, body.top() + h * 0.04), QPointF(x, body.top() + h * 0.2))
    else:
        p.drawRoundedRect(palm, w * 0.14, w * 0.14)
        fingers = [(0.25, 0.08)] if kind == 'point' else [(0.23, 0.12), (0.37, 0.05), (0.51, 0.08), (0.65, 0.16)]
        for fx, fy in fingers:
            p.drawRoundedRect(QRectF(x0 + w * fx, y0 + h * fy, fw, h * 0.5), fw / 2, fw / 2)
        if kind == 'point':
            p.drawRoundedRect(QRectF(x0 + w * 0.4, y0 + h * 0.36, w * 0.36, h * 0.2), fw / 2, fw / 2)
        else:
            path = QPainterPath()
            path.addRoundedRect(QRectF(x0 + w * 0.02, y0 + h * 0.46, fw, h * 0.32), fw / 2, fw / 2)
            p.translate(x0 + w * 0.1, y0 + h * 0.62)
            p.rotate(-35)
            p.translate(-(x0 + w * 0.1), -(y0 + h * 0.62))
            p.drawPath(path)
    p.restore()


class GestureIcon(QWidget):
    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.active = False
        self.setFixedSize(40, 40)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = S.qc(S.STRONG) if self.active else S.qc(S.ELEVATED)
        p.setPen(Qt.PenStyle.NoPen if self.active else QPen(S.qc(S.BORDER), 1))
        p.setBrush(bg)
        p.drawEllipse(QRectF(0.5, 0.5, 39, 39))
        draw_gesture_icon(p, QRectF(9, 8, 22, 24), self.kind,
                          S.qc(S.CARD) if self.active else S.qc(S.SECONDARY), cut=bg)


class HintRow(QWidget):
    """「食指寫字／張開手掌換筆／握拳送出」；目前的手勢會亮起。"""
    ITEMS = [('point', 'Point to write'), ('open', 'Open hand: next stroke'), ('fist', 'Fist: send')]

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(8)
        self.icons = {}
        self.labels = {}
        for kind, text in self.ITEMS:
            cell = QHBoxLayout()
            cell.setSpacing(8)
            icon = GestureIcon(kind)
            lab = QLabel(text)
            lab.setFont(S.font(15, QFont.Weight.DemiBold))
            lab.setStyleSheet(f'color: {S.SECONDARY};')
            lab.setWordWrap(True)
            cell.addWidget(icon)
            cell.addWidget(lab, 1)
            lay.addLayout(cell)
            self.icons[kind] = icon
            self.labels[kind] = lab

    def set_active(self, gesture: str):
        for kind, icon in self.icons.items():
            on = kind == gesture
            if icon.active != on:
                icon.active = on
                icon.update()
                self.labels[kind].setStyleSheet(f'color: {S.STRONG if on else S.SECONDARY};')


def _fit(src_w: float, src_h: float, dst: QRectF) -> QRectF:
    if src_w <= 0 or src_h <= 0:
        return dst
    scale = min(dst.width() / src_w, dst.height() / src_h)
    w, h = src_w * scale, src_h * scale
    return QRectF(dst.left() + (dst.width() - w) / 2, dst.top() + (dst.height() - h) / 2, w, h)


class VideoView(QWidget):
    """水平鏡像的鏡頭畫面，疊上手部骨架、指尖、筆跡與握拳進度。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image: Optional[QImage] = None
        self.hand: dict = {}
        self.strokes: List[Sequence] = []
        self.pen_down = False
        self.placeholder = 'Waiting for camera...'
        self.placeholder_tone = 'neutral'
        self.source_label = ''
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(320, 240)

    def sizeHint(self):
        # 偏好高度保持小：捲動區域以偏好高度計算頁面高度，多出的空間仍會分給預覽
        return QSize(640, 300)

    def set_image(self, img: Optional[QImage]):
        self.image = img
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(rect, 16, 16)
        p.setClipPath(clip)
        has_img = self.image is not None and not self.image.isNull()
        p.fillRect(rect, S.qc(S.VIDEO_BG) if has_img else S.qc(S.ELEVATED))
        iw, ih = (self.image.width(), self.image.height()) if has_img else (640, 480)
        area = _fit(iw, ih, rect)
        if has_img:
            p.drawImage(area, self.image)
        else:
            p.setPen(S.qc(S.TONE.get(self.placeholder_tone, S.SECONDARY)))
            p.setFont(S.font(17, QFont.Weight.DemiBold))
            p.drawText(rect.adjusted(24, 0, -24, 0),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.placeholder)

        def to_px(x, y):
            return QPointF(area.left() + x * area.width(), area.top() + y * area.height())

        # 書寫範圍：指尖寫到畫面下方時手掌會出界、追蹤中斷，所以建議在中上部書寫
        if has_img:
            zx0, zy0, zx1, zy1 = WRITING_ZONE
            zone = QRectF(to_px(zx0, zy0), to_px(zx1, zy1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(S.qc('#ffffff', 130), 1.5, Qt.PenStyle.DashLine))
            p.drawRoundedRect(zone, 14, 14)
            p.setFont(S.mono(12))
            p.setPen(S.qc('#ffffff', 190))
            p.drawText(QPointF(zone.left() + 12, zone.bottom() - 10), 'Write inside this box')

        # 筆跡
        n = len(self.strokes)
        for i, s in enumerate(self.strokes):
            if len(s) < 2:
                continue
            path = QPainterPath(to_px(*s[0]))
            for pt in s[1:]:
                path.lineTo(to_px(*pt))
            live = self.pen_down and i == n - 1
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(S.qc('#ffffff', 220), 10, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin))
            p.drawPath(path)
            p.setPen(QPen(S.qc(S.TEAL_BRIGHT if live else S.STRONG), 5.5, cap=Qt.PenCapStyle.RoundCap,
                          join=Qt.PenJoinStyle.RoundJoin))
            p.drawPath(path)

        hand = self.hand or {}
        lx, ly = hand.get('landmarks_x') or [], hand.get('landmarks_y') or []
        if has_img and len(lx) == 21:
            pts = [to_px(lx[i], ly[i]) for i in range(21)]
            p.setPen(QPen(S.qc('#ffffff', 170), 2))
            for a, b in HAND_EDGES:
                p.drawLine(pts[a], pts[b])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(S.qc('#ffffff', 220))
            for q in pts:
                p.drawEllipse(q, 2.6, 2.6)
        tip = hand.get('tip')
        if hand.get('hand_visible') and tip and tip[0] >= 0:
            c = to_px(*tip)
            down = hand.get('pen') == 'down'
            p.setPen(QPen(S.qc('#ffffff'), 3))
            p.setBrush(S.qc(S.TEAL_BRIGHT) if down else S.qc('#ffffff', 70))
            p.drawEllipse(c, 10, 10)
            prog = float(hand.get('fist_progress') or 0)
            if prog > 0:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(S.qc('#ffffff', 150), 6))
                ring = QRectF(c.x() - 26, c.y() - 26, 52, 52)
                p.drawEllipse(ring)
                p.setPen(QPen(S.qc(S.YELLOW_BRIGHT), 6, cap=Qt.PenCapStyle.RoundCap))
                p.drawArc(ring, 90 * 16, -int(360 * 16 * prog))

        p.setClipping(False)
        # 左上角狀態膠囊
        chip = self._chip_text(hand)
        if chip and has_img:
            self._draw_chip(p, QPointF(14, 14), *chip)
        if has_img:
            label = 'Mirrored' + (f' · {self.source_label}' if self.source_label else '')
            p.setFont(S.mono(12))
            fm = p.fontMetrics()
            w = fm.horizontalAdvance(label) + 22
            r = QRectF(rect.right() - w - 12, rect.bottom() - 36, w, 24)
            p.setPen(QPen(S.qc(S.BORDER), 1))
            p.setBrush(S.qc(S.CARD, 230))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(S.qc(S.SECONDARY))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)

    @staticmethod
    def _chip_text(hand: dict):
        if not hand:
            return None
        if not hand.get('hand_visible'):
            return ('No hand', S.SECONDARY)
        pen = hand.get('pen')
        g = hand.get('gesture')
        if pen == 'locked':
            return ('Sent', S.INDIGO)
        if pen == 'down':
            return ('Writing', S.GREEN)
        if g == 'fist':
            return (f'Fist {int(float(hand.get("fist_progress") or 0) * 100)}%', S.ORANGE)
        if g == 'open':
            return ('Pen up', S.LABEL)
        if g == 'point':
            return ('Ready', S.GREEN)
        return ('Pen up', S.SECONDARY)

    @staticmethod
    def _draw_chip(p: QPainter, at: QPointF, text: str, color: str):
        p.setFont(S.mono(14, QFont.Weight.DemiBold))
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 38
        r = QRectF(at.x(), at.y(), w, 32)
        p.setPen(QPen(S.qc(S.BORDER), 1))
        p.setBrush(S.qc(S.CARD, 240))
        p.drawRoundedRect(r, 16, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(S.qc(color))
        p.drawEllipse(QPointF(r.left() + 15, r.center().y()), 5, 5)
        p.setPen(S.qc(S.STRONG))
        p.drawText(r.adjusted(27, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


class InkView(QWidget):
    """放大顯示筆跡，並以方框標出每個數字的分組（辨識後附上結果）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.strokes: List[Sequence] = []
        self.digits: List[dict] = []
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(340, 136)

    def set_data(self, strokes, digits):
        self.strokes = strokes
        self.digits = digits
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        p.setPen(QPen(S.qc(S.BORDER), 1))
        p.setBrush(S.qc(S.PAPER))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        p.setPen(QPen(S.qc(S.PAPER_LINE), 1, Qt.PenStyle.DotLine))
        for i in range(1, 4):
            y = rect.top() + rect.height() * i / 4
            p.drawLine(QPointF(rect.left() + 10, y), QPointF(rect.right() - 10, y))
        pts = [pt for s in self.strokes for pt in s]
        if len(pts) < 2:
            p.setPen(S.qc(S.SECONDARY))
            p.setFont(S.font(14))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, 'Your writing shows up here')
            return
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        inner = rect.adjusted(18, 26, -18, -14)
        span = max(x1 - x0, y1 - y0, 1e-3)
        scale = min(inner.width() / max(x1 - x0, 1e-3), inner.height() / max(y1 - y0, 1e-3), inner.height() / span * 1.6)
        ox = inner.left() + (inner.width() - (x1 - x0) * scale) / 2
        oy = inner.top() + (inner.height() - (y1 - y0) * scale) / 2

        def m(x, y):
            return QPointF(ox + (x - x0) * scale, oy + (y - y0) * scale)

        groups = self.digits
        if not groups:
            groups = [{'x_min': g.x_min, 'x_max': g.x_max, 'y_min': g.y_min, 'y_max': g.y_max,
                       'stroke_indices': g.stroke_indices, 'label': ''} for g in group_strokes(self.strokes)]
        color_of = {}
        for k, g in enumerate(groups):
            col = DIGIT_COLORS[k % len(DIGIT_COLORS)]
            for si in g.get('stroke_indices', []):
                color_of[si] = col
            box = QRectF(m(g['x_min'], g['y_min']), m(g['x_max'], g['y_max'])).adjusted(-7, -7, 7, 7)
            p.setPen(QPen(S.qc(col, 110), 1.5, Qt.PenStyle.DashLine))
            p.setBrush(S.qc(col, 14))
            p.drawRoundedRect(box, 8, 8)
            label = g.get('label') or f'#{k + 1}'
            p.setFont(S.mono(12, QFont.Weight.Bold))
            p.setPen(S.qc(col))
            p.drawText(QPointF(box.left() + 2, box.top() - 5), label)
        for i, s in enumerate(self.strokes):
            if len(s) < 2:
                continue
            path = QPainterPath(m(*s[0]))
            for q in s[1:]:
                path.lineTo(m(*q))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(S.qc(color_of.get(i, S.INK)), 3.5, cap=Qt.PenCapStyle.RoundCap,
                          join=Qt.PenJoinStyle.RoundJoin))
            p.drawPath(path)


class RoundedProgress(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.color = S.BLUE
        self.setFixedHeight(8)

    def set_value(self, v: float, color: str = S.BLUE):
        self.value = max(0.0, min(1.0, v))
        self.color = color
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(S.qc(S.BORDER))
        p.drawRoundedRect(r, 4, 4)
        if self.value > 0:
            p.setBrush(QBrush(S.qc(self.color)))
            p.drawRoundedRect(QRectF(r.left(), r.top(), max(10.0, r.width() * self.value), r.height()), 5, 5)
