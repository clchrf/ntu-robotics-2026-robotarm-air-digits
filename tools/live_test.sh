#!/usr/bin/env bash
# 實測：啟動完整程式（介面顯示在桌面）＋背景紀錄。關閉介面視窗即結束。
source /opt/ros/jazzy/setup.bash; source ~/digit_arm_ws/install/setup.bash
cd "$(dirname "$0")/.."
python3 -u tools/record_session.py > /tmp/digit_arm_session.log 2>&1 &
REC=$!
ros2 launch digit_arm digit_arm.launch.py "$@" > /tmp/digit_arm_live.log 2>&1
kill -INT $REC 2>/dev/null; wait $REC 2>/dev/null
echo "結束"
