#!/usr/bin/env bash
# 在 WSL 中啟動全部節點與介面。參數會轉給 ros2 launch，例如：
#   scripts/launch.sh                       模擬模式
#   scripts/launch.sh mode:=hardware        實體手臂（需先填好設定檔中的動作）
set -eo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WS="${DIGIT_ARM_WS:-$HOME/digit_arm_ws}"
source /opt/ros/jazzy/setup.bash
if [ ! -f "$WS/install/setup.bash" ] || [ ! -d "$WS/install/digit_arm" ]; then
  echo "第一次執行：建置工作區…"
  "$PROJECT_DIR/scripts/setup_ws.sh"
fi
source "$WS/install/setup.bash"
exec ros2 launch digit_arm digit_arm.launch.py "$@"
