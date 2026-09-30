"""Windows COM 埠 ↔ 標準輸入輸出的逐行轉送（由 motion_executor_node 啟動）。

這支程式不理解、也不產生任何手臂指令；只把收到的「TX <內容>」原樣寫到序列埠，
並把序列埠每一行以「RX <內容>」輸出。控制邏輯全部在 ROS 節點。

stdout：@@OPEN <port> <描述> ／ @@ERR <原因> ／ @@CLOSED ／ RX <行>
stdin ：TX <行> ／ CLOSE（或 stdin 關閉）
--port auto：只有「剛好一個」看起來像 Arduino／CH340 的序列埠時才使用它。
"""

from __future__ import annotations

import argparse
import sys
import threading

HINTS = ('Arduino', 'CH340', 'CH341', 'USB-SERIAL', 'USB Serial', 'USB 序列')


def out(line: str):
    sys.stdout.write(line + '\n')
    sys.stdout.flush()


def pick_port(requested: str):
    if '://' in requested:
        return requested, 'pyserial URL (test)'
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    if requested.lower() != 'auto':
        for p in ports:
            if p.device.lower() == requested.lower():
                return p.device, p.description
        return requested, ''
    candidates = [p for p in ports if any(h.lower() in (p.description or '').lower() for h in HINTS)]
    if not candidates:
        found = ', '.join(f'{p.device} ({p.description})' for p in ports) or 'no serial ports'
        raise RuntimeError(f'Cannot find the arm serial port (found: {found}). Check the USB cable, and that usbipd did not move it to WSL.')
    if len(candidates) > 1:
        names = ', '.join(p.device for p in candidates)
        raise RuntimeError(f'Found more than one possible port: {names}. Set port in the config, for example win:COM5')
    return candidates[0].device, candidates[0].description


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8', newline='\n')
        sys.stdin.reconfigure(encoding='utf-8')
    except AttributeError:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', default='auto')
    ap.add_argument('--baud', type=int, default=115200)
    args = ap.parse_args(argv)

    try:
        import serial
    except ImportError:
        out('@@ERR pyserial is missing on Windows (pip install pyserial)')
        return 2
    try:
        device, desc = pick_port(args.port)
        ser = serial.serial_for_url(device, baudrate=args.baud, timeout=0.05, write_timeout=1.0)
    except Exception as exc:  # noqa: BLE001
        out(f'@@ERR Cannot open serial port: {exc}')
        return 3
    out(f'@@OPEN {device} {desc}'.rstrip())

    stop = threading.Event()
    lock = threading.Lock()

    def reader():
        buf = b''
        while not stop.is_set():
            try:
                chunk = ser.read(256)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    out(f'@@ERR Serial port lost: {exc}')
                stop.set()
                return
            if not chunk:
                continue
            buf += chunk
            while b'\n' in buf:
                raw, buf = buf.split(b'\n', 1)
                with lock:
                    out('RX ' + raw.decode(errors='ignore').strip())

    threading.Thread(target=reader, daemon=True).start()
    try:
        for line in sys.stdin:
            if stop.is_set():
                break
            line = line.rstrip('\r\n')
            if line == 'CLOSE':
                break
            if line.startswith('TX '):
                try:
                    ser.write((line[3:] + '\n').encode('ascii'))
                    ser.flush()
                except Exception as exc:  # noqa: BLE001
                    with lock:
                        out(f'@@ERR Could not write to serial port: {exc}')
                    break
    finally:
        stop.set()
        try:
            ser.close()
        except Exception:  # noqa: BLE001
            pass
        with lock:
            out('@@CLOSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
