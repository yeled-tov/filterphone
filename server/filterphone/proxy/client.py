"""לקוח למנוע הסינון: מאגר חיבורים קבועים ל-Unix socket."""
from __future__ import annotations

import asyncio

from .. import protocol

Conn = tuple[asyncio.StreamReader, asyncio.StreamWriter]


class EngineClient:
    def __init__(self, path: str, pool_size: int, timeout_s: float):
        self._path = path
        self._timeout = timeout_s
        self._sem = asyncio.Semaphore(pool_size)
        self._idle: list[Conn] = []

    async def classify(self, body: bytes, header: dict) -> tuple[dict, bytes]:
        async with self._sem:
            conn = self._idle.pop() if self._idle else await asyncio.open_unix_connection(self._path)
            try:
                result = await asyncio.wait_for(self._roundtrip(conn, header, body), self._timeout)
            except BaseException:
                conn[1].close()         # חיבור במצב לא ידוע — לא מחזירים למאגר
                raise
            self._idle.append(conn)
            return result

    @staticmethod
    async def _roundtrip(conn: Conn, header: dict, body: bytes) -> tuple[dict, bytes]:
        reader, writer = conn
        writer.write(protocol.encode(header, body))
        await writer.drain()
        return await protocol.read_message(reader)
