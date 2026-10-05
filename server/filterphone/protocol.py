"""פרוטוקול התקשורת בין הפרוקסי למנוע, על גבי Unix socket.

כל הודעה (בקשה או תשובה):
    [4 בתים: אורך הכותרת][4 בתים: אורך הגוף][כותרת JSON][גוף בינארי]

בקשה  — כותרת: {"level": 1, "ct": "image/jpeg", "host": "...", "client": "10.8.0.7"}, גוף: התמונה.
תשובה — כותרת: {"action": "pass|block|replace", "reason": "...", "ct": "...", "ms": 12.3, "cached": true},
        גוף: התמונה החדשה (רק כש-action == "replace").
"""
from __future__ import annotations

import asyncio
import json
import struct

_PREFIX = struct.Struct(">II")
MAX_HEADER = 64 * 1024
MAX_BODY = 32 * 1024 * 1024


class ProtocolError(Exception):
    pass


def encode(header: dict, body: bytes = b"") -> bytes:
    h = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode()
    return _PREFIX.pack(len(h), len(body)) + h + body


async def read_message(reader: asyncio.StreamReader) -> tuple[dict, bytes]:
    prefix = await reader.readexactly(_PREFIX.size)
    hlen, blen = _PREFIX.unpack(prefix)
    if hlen > MAX_HEADER or blen > MAX_BODY:
        raise ProtocolError(f"message too large: header={hlen} body={blen}")
    header = json.loads(await reader.readexactly(hlen)) if hlen else {}
    body = await reader.readexactly(blen) if blen else b""
    return header, body
