@echo off
rem 一鍵啟動（Windows）：在 WSL Ubuntu-24.04 中執行 ROS 2 Jazzy 的全部節點與介面。
rem 用法：Start-DigitArm.cmd                 模擬模式
rem       Start-DigitArm.cmd mode:=hardware  實體手臂
chcp 65001 >nul
wsl -d Ubuntu-24.04 --cd "%~dp0" -- bash scripts/launch.sh %*
if errorlevel 1 pause
