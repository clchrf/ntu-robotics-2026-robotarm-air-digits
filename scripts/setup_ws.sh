#!/usr/bin/env bash
# 在 WSL Ubuntu-24.04 建立／更新 ~/digit_arm_ws 並建置。
# 原始碼留在 Windows 專案資料夾，工作區的 src 是指向它的連結。
set -eo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WS="${DIGIT_ARM_WS:-$HOME/digit_arm_ws}"

source /opt/ros/jazzy/setup.bash
mkdir -p "$WS"
ln -sfn "$PROJECT_DIR/ros2_ws/src" "$WS/src"
cd "$WS"
colcon build --symlink-install "$@"
echo
echo "建置完成：$WS"
echo "啟動：$PROJECT_DIR/scripts/launch.sh            （模擬模式）"
