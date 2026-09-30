"""Windows 序列埠轉送檢查（WSL）：不接手臂，用 pyserial 的 loop:// 迴路裝置驗證逐行轉送，
並確認找不到手臂時會回報清楚的錯誤。不會開啟任何實體 COM 埠以外的裝置，也不送任何手臂指令。"""
import sys

from digit_arm.core import winproc
from digit_arm.core.serial_link import LinkError, WindowsBridgeLink

py = winproc.resolve_windows_python('python.exe')
script = winproc.to_windows_path(winproc.worker_script('serial_bridge.py'))
ok = True

link = WindowsBridgeLink(py, script, 'loop://', 115200)
link.open()
print('開啟：', link.description)
for text in ('hello', '0.0,0.0,0.0,90.0'):
    link.write_line(text)
    got = link.read_line(3.0)
    print(f'送出 {text!r} → 收到 {got!r}')
    ok &= got == text
link.close()
print('關閉後 is_open =', link.is_open)
ok &= not link.is_open

link = WindowsBridgeLink(py, script, 'auto', 115200)
try:
    link.open()
    print('auto：已開啟', link.description, '（有接手臂？）')
    link.close()
except LinkError as exc:
    print('auto（未接手臂）：', exc)
    ok &= '找不到' in str(exc) or '多個' in str(exc)
print('結果：' + ('通過' if ok else '未通過'))
sys.exit(0 if ok else 1)
