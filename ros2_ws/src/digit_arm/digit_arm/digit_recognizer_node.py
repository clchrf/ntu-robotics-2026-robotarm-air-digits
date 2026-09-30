"""筆跡與數字辨識節點。

訂閱 /digit_arm/ink/submitted（握拳送出的筆跡），發布 /digit_arm/recognition（latched）。
辨識失敗或不確定時 success=false 並附上繁體中文原因；動作節點只接受 success=true 的結果。
"""

from __future__ import annotations

import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

from digit_arm_interfaces.msg import DigitGuess, Ink, RecognitionResult

from .core.recognizer import DigitRecognizer, RecognizerConfig

LATCHED = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST,
                     reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)


class DigitRecognizerNode(Node):
    def __init__(self):
        super().__init__('digit_recognizer_node')
        self.declare_parameter('max_count', 20)
        self.declare_parameter('max_digits', 3)
        self.declare_parameter('min_confidence', 0.70)
        cfg = RecognizerConfig(max_count=int(self.get_parameter('max_count').value),
                               max_digits=int(self.get_parameter('max_digits').value),
                               min_confidence=float(self.get_parameter('min_confidence').value))
        self.recognizer = DigitRecognizer(cfg)
        self.pub = self.create_publisher(RecognitionResult, '/digit_arm/recognition', LATCHED)
        self.create_subscription(Ink, '/digit_arm/ink/submitted', self._on_ink, 10)
        self.get_logger().info(f'Digit recognizer ready (max {cfg.max_count}, up to {cfg.max_digits} digits)')

    def _on_ink(self, ink: Ink):
        if ink.interrupted:
            # 實測：寫到畫面下方時手掌出界、追蹤中斷，殘缺的 3 被高信心認成 9。筆跡不完整就不猜。
            msg = RecognitionResult(ink_id=ink.ink_id, success=False,
                                    reason='Your hand left the camera while writing, so the stroke is not complete. '
                                           'Please clear and write inside the box.')
            self.pub.publish(msg)
            self.get_logger().info(f'ink {ink.ink_id}: stroke broken by tracking loss, not read')
            return
        strokes = [list(zip(s.x, s.y)) for s in ink.strokes]
        t0 = time.monotonic()
        try:
            rec = self.recognizer.recognize(strokes)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f'Recognition error: {exc}')
            msg = RecognitionResult(ink_id=ink.ink_id, success=False, reason=f'Recognition error: {exc}')
            self.pub.publish(msg)
            return
        msg = RecognitionResult()
        msg.ink_id = ink.ink_id
        msg.success = bool(rec.success)
        msg.value = int(rec.value) if rec.value is not None else 0
        msg.text = rec.text
        msg.confidence = float(rec.confidence)
        msg.reason = rec.reason
        for d in rec.digits:
            g = DigitGuess()
            best = max(d.scores, key=d.scores.get) if d.scores else ''
            g.label = d.label or ''
            g.confidence = float(d.scores.get(best, 0.0))
            g.runner_up = d.runner_up if d.label else best
            g.x_min, g.x_max = float(d.group.x_min), float(d.group.x_max)
            g.y_min, g.y_max = float(d.group.y_min), float(d.group.y_max)
            g.stroke_indices = [int(i) for i in d.group.stroke_indices]
            msg.digits.append(g)
        self.pub.publish(msg)
        took = (time.monotonic() - t0) * 1000
        if rec.success:
            self.get_logger().info(f'ink {ink.ink_id}: read {rec.value} (confidence {rec.confidence:.2f}, {took:.0f} ms)')
        else:
            self.get_logger().info(f'ink {ink.ink_id}: rejected - {rec.reason} ({took:.0f} ms)')


def main(args=None):
    rclpy.init(args=args)
    node = DigitRecognizerNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
