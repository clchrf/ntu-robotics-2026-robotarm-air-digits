"""以「行」為單位的序列連線。

- PySerialLink：直接開啟序列埠（Linux 的 /dev/ttyUSB0，或 usbipd 轉進 WSL 的裝置）。
- WindowsBridgeLink：在 WSL 中啟動 Windows 的 workers/serial_bridge.py，
  由它開 COM 埠並原樣轉送每一行（不會自行送出任何內容）。
兩者都在背景執行緒讀取，read_line 可設定逾時，斷線時拋出 LinkError。
"""

from __future__ import annotations

import queue
import subprocess
import threading
import time
from typing import Callable, List, Optional


class LinkError(Exception):
    pass


_BROKEN = object()


class LineLink:
    description = ''

    def __init__(self, log: Optional[Callable[[str], None]] = None):
        self._q: 'queue.Queue' = queue.Queue()
        self._broken: Optional[str] = None
        self._log = log or (lambda _m: None)

    @property
    def is_open(self) -> bool:
        raise NotImplementedError

    @property
    def broken_reason(self) -> Optional[str]:
        return self._broken

    def open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def write_line(self, text: str) -> None:
        raise NotImplementedError

    def _mark_broken(self, reason: str):
        if self._broken is None:
            self._broken = reason
            self._q.put((_BROKEN, reason))

    def read_line(self, timeout: float) -> Optional[str]:
        try:
            item = self._q.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        if isinstance(item, tuple) and item and item[0] is _BROKEN:
            self._q.put(item)  # 之後的讀取也要看到斷線
            raise LinkError(item[1])
        return item

    def drain(self) -> List[str]:
        lines = []
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                return lines
            if isinstance(item, tuple) and item and item[0] is _BROKEN:
                self._q.put(item)
                raise LinkError(item[1])
            lines.append(item)


class PySerialLink(LineLink):
    def __init__(self, port: str, baud: int, log=None):
        super().__init__(log)
        self.port = port
        self.baud = baud
        self.description = port
        self._ser = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._broken is None

    def open(self) -> None:
        import serial  # pyserial
        try:
            self._ser = serial.serial_for_url(self.port, baudrate=self.baud, timeout=0.05, write_timeout=1.0)
        except Exception as exc:  # noqa: BLE001
            raise LinkError(f'Cannot open serial port {self.port}: {exc}') from exc
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self):
        buf = b''
        while not self._stop.is_set():
            try:
                chunk = self._ser.read(256)
            except Exception as exc:  # noqa: BLE001
                self._mark_broken(f'Serial port lost: {exc}')
                return
            if not chunk:
                continue
            buf += chunk
            while b'\n' in buf:
                raw, buf = buf.split(b'\n', 1)
                self._q.put(raw.decode(errors='ignore').strip())

    def write_line(self, text: str) -> None:
        if not self.is_open:
            raise LinkError(self._broken or 'Serial port not open')
        try:
            self._ser.write((text + '\n').encode('ascii'))
            self._ser.flush()
        except Exception as exc:  # noqa: BLE001
            self._mark_broken(f'Could not write to serial port: {exc}')
            raise LinkError(self._broken) from exc

    def close(self) -> None:
        self._stop.set()
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._ser = None
        self._mark_broken('Closed')


class WindowsBridgeLink(LineLink):
    """透過 Windows 端 serial_bridge.py 使用 COM 埠。"""

    def __init__(self, python_exe: str, script_win_path: str, port: str, baud: int,
                 open_timeout: float = 8.0, log=None):
        super().__init__(log)
        self.python_exe = python_exe
        self.script = script_win_path
        self.port = port
        self.baud = baud
        self.open_timeout = open_timeout
        self.description = f'Windows {port}'
        self._proc: Optional[subprocess.Popen] = None
        self._opened = threading.Event()
        self._open_error: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        return self._proc is not None and self._proc.poll() is None and self._broken is None

    def open(self) -> None:
        cmd = [self.python_exe, '-u', self.script, '--port', self.port, '--baud', str(self.baud)]
        try:
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                          errors='replace', bufsize=1)
        except OSError as exc:
            raise LinkError(f'Cannot start the Windows serial helper: {exc}') from exc
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._stderr_reader, daemon=True).start()
        if not self._opened.wait(self.open_timeout):
            self.close()
            raise LinkError(self._open_error or f'Timed out opening {self.port}')
        if self._open_error:
            self.close()
            raise LinkError(self._open_error)

    def _reader(self):
        assert self._proc is not None and self._proc.stdout is not None
        for raw in self._proc.stdout:
            line = raw.rstrip('\r\n')
            if line.startswith('RX '):
                self._q.put(line[3:].strip())
            elif line == 'RX':
                self._q.put('')
            elif line.startswith('@@OPEN'):
                self.description = 'Windows ' + line[6:].strip()
                self._opened.set()
            elif line.startswith('@@ERR'):
                msg = line[5:].strip()
                if not self._opened.is_set():
                    self._open_error = msg
                    self._opened.set()
                self._mark_broken(msg)
            elif line.startswith('@@CLOSED'):
                self._mark_broken('Serial port closed')
        if not self._opened.is_set():
            self._open_error = self._open_error or 'The Windows serial helper stopped unexpectedly'
            self._opened.set()
        self._mark_broken(self._broken or 'The serial helper has stopped')

    def _stderr_reader(self):
        assert self._proc is not None and self._proc.stderr is not None
        for raw in self._proc.stderr:
            self._log('[serial_bridge] ' + raw.rstrip())

    def write_line(self, text: str) -> None:
        if not self.is_open:
            raise LinkError(self._broken or 'Serial port not open')
        with self._lock:
            try:
                self._proc.stdin.write('TX ' + text + '\n')
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self._mark_broken(f'Write failed: {exc}')
                raise LinkError(self._broken) from exc

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin:
                proc.stdin.write('CLOSE\n')
                proc.stdin.flush()
                proc.stdin.close()
        except (OSError, ValueError):
            pass
        deadline = time.monotonic() + 2.0
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if proc.poll() is None:
            proc.kill()
        self._mark_broken('Closed')
