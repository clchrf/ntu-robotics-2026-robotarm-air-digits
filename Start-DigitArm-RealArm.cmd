@echo off
rem Real arm mode: same as Start-DigitArm.cmd, but talks to the robot arm over USB.
call "%~dp0Start-DigitArm.cmd" mode:=hardware %*
