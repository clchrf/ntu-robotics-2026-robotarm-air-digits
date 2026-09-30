"""在 WSL 中啟動 Windows 端的小工作程式（鏡頭、序列埠）。

WSL2 看不到筆電鏡頭，序列埠也預設留在 Windows；ROS 節點透過 WSL interop
執行 Windows 的 python.exe，並以標準輸入／輸出溝通。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def in_wsl() -> bool:
    try:
        return 'microsoft' in Path('/proc/version').read_text().lower()
    except OSError:
        return False


def to_windows_path(path: str | os.PathLike) -> str:
    """把路徑轉成 Windows python.exe 可以讀的形式。"""
    real = os.path.realpath(str(path))
    if os.name == 'nt':
        return real
    if shutil.which('wslpath'):
        out = subprocess.run(['wslpath', '-w', real], capture_output=True, text=True, check=True)
        return out.stdout.strip()
    return real


def resolve_windows_python(configured: str) -> str:
    exe = configured or 'python.exe'
    found = shutil.which(exe)
    if found:
        return found
    if os.path.isfile(exe):
        return exe
    raise FileNotFoundError(
        f'Cannot find Windows Python ({exe}). Set the full path in windows_python in the config, '
        'for example /mnt/c/Users/<name>/AppData/Local/Programs/Python/Python312/python.exe')


def worker_script(name: str) -> Path:
    return Path(__file__).resolve().parent.parent / 'workers' / name
