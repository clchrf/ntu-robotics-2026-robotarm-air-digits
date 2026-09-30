#!/usr/bin/env bash
# 完整 launch 煙霧測試（含介面，offscreen）：執行 N 秒後送 Ctrl+C，檢查例外與結束狀態。
# 用法：tools/launch_smoke.sh <秒數> [launch 參數...]

SECS=${1:-20}; shift || true
source /opt/ros/jazzy/setup.bash; source ~/digit_arm_ws/install/setup.bash
if pgrep -f digit_arm/lib/digit_arm >/dev/null; then echo "偵測到「空中寫數字」正在執行：請先關閉再測試。"; exit 2; fi
LOG=$(mktemp /tmp/digit_arm_smoke_XXXX.log)
# 非互動 bash 的背景工作會忽略 SIGINT 並遺傳給子行程；先恢復預設再 exec launch
QT_QPA_PLATFORM=offscreen setsid python3 -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp("ros2", ["ros2", "launch", "digit_arm", "digit_arm.launch.py"] + sys.argv[1:])' "$@" > "$LOG" 2>&1 &
PID=$!
sleep "$SECS"
kill -INT "$PID"
for i in $(seq 1 40); do sleep 0.5; kill -0 "$PID" 2>/dev/null || break; done
kill -0 "$PID" 2>/dev/null && { echo "!! launch 未在 20 秒內結束，強制終止"; kill -9 -"$PID"; } || echo "launch 已正常結束"
echo "=== 重點記錄 ==="
grep -E "就緒|辨識為|未通過|已送出|拒絕|ERROR|Traceback|Exception|finished|died" "$LOG" | grep -v "process started" | sed 's/\[[0-9.]*\] //' | head -30
echo "=== 例外數：$(grep -c -E 'Traceback|Exception' "$LOG")，異常結束：$(grep -c 'process has died' "$LOG")"
echo "=== 殘留行程：$(pgrep -fc 'digit_arm/lib/digit_arm' || true)"
