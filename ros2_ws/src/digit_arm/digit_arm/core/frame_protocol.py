"""鏡頭工作程式 → ROS 節點的二進位封包。

  b'DAF1' | uint32 json 長度 | uint32 jpeg 長度 | json（UTF-8）| jpeg

json 的 type：
  frame  — seq、t（秒）、w、h、hands（[{landmarks: [[x,y,z]*21], handedness, score}]）
  status — state（opened）、message、backend
  error  — code、message
"""

from __future__ import annotations

import json
import struct
from typing import BinaryIO, Optional, Tuple

MAGIC = b'DAF1'
_HDR = struct.Struct('<4sII')
MAX_JSON = 1 << 20
MAX_JPEG = 16 << 20


def pack(meta: dict, jpeg: bytes = b'') -> bytes:
    body = json.dumps(meta, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return _HDR.pack(MAGIC, len(body), len(jpeg)) + body + jpeg


def _read_exact(stream: BinaryIO, n: int) -> Optional[bytes]:
    buf = b''
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def read_packet(stream: BinaryIO) -> Optional[Tuple[dict, bytes]]:
    """讀一個封包；串流結束回傳 None；格式錯誤拋出 ValueError。"""
    hdr = _read_exact(stream, _HDR.size)
    if hdr is None:
        return None
    magic, jlen, ilen = _HDR.unpack(hdr)
    if magic != MAGIC or jlen > MAX_JSON or ilen > MAX_JPEG:
        raise ValueError('Bad camera data format')
    body = _read_exact(stream, jlen)
    img = _read_exact(stream, ilen) if ilen else b''
    if body is None or img is None:
        return None
    return json.loads(body.decode('utf-8')), img
