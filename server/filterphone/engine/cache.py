"""קאש תוצאות לפי hash של התמונה, ב-Redis.

נשמרים ציונים גולמיים ולא החלטות — כך רשומה אחת משרתת את כל רמות הסינון,
ושינוי רמה או ספים חל מיד. גרסת ה-heads היא חלק מהמפתח: מודל חדש = קאש חדש.

מפת הסגמנטציה (רמה 2) נשמרת בנפרד, כ-PNG קטן: צביעה חוזרת של אותה תמונה
לא מריצה את המודל שוב.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

import redis.asyncio as aioredis

from .segment import FORMAT as SEG_FORMAT

log = logging.getLogger(__name__)

SEG_TTL_DAYS = 7


def image_key(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=20).hexdigest()


@dataclass(slots=True)
class Entry:
    scores: list[float] | None          # None = תמונה קטנה/לא מתפענחת
    dets: list[list] | None = None      # תוצאות NudeNet, אם חושבו
    note: str = ""                      # "tiny" / "undecodable" / "detector_failed"
    seg: dict | None = None             # סטטיסטיקות סגמנטציה (רמה 2), אם חושבו
    wh: list[int] | None = None         # מידות התמונה — לתמונה חלופית באותו גודל

    def dump(self) -> str:
        d: dict = {"s": self.scores}
        if self.dets is not None:
            d["n"] = self.dets
        if self.note:
            d["x"] = self.note
        if self.seg is not None:
            d["g"] = self.seg
        if self.wh is not None:
            d["d"] = self.wh
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def load(cls, raw: str) -> Entry:
        d = json.loads(raw)
        return cls(d.get("s"), d.get("n"), d.get("x", ""), d.get("g"), d.get("d"))

    def with_(self, **changes) -> Entry:
        return Entry(**{**{f: getattr(self, f) for f in self.__slots__}, **changes})


class ResultCache:
    def __init__(self, redis_url: str, namespace: str, ttl_days: int):
        self._r = aioredis.from_url(redis_url, decode_responses=True,
                                    socket_timeout=0.5, socket_connect_timeout=0.5)
        self._rb = aioredis.from_url(redis_url, decode_responses=False,
                                     socket_timeout=0.5, socket_connect_timeout=0.5)
        self._ns = f"fp:img:{namespace}:"
        self._seg_ns = f"fp:seg{SEG_FORMAT}:{namespace}:"      # מפות בפורמט ישן (בלי הפרדה לנשים) — לא נקראות
        self._ttl = ttl_days * 86400

    async def get(self, key: str) -> Entry | None:
        try:
            raw = await self._r.get(self._ns + key)
            return Entry.load(raw) if raw else None
        except Exception as e:      # Redis לא זמין — ממשיכים בלי קאש
            log.debug("cache get failed: %s", e)
            return None

    async def put(self, key: str, entry: Entry) -> None:
        try:
            await self._r.set(self._ns + key, entry.dump(), ex=self._ttl)
        except Exception as e:
            log.debug("cache put failed: %s", e)

    async def get_labels(self, key: str) -> bytes | None:
        try:
            return await self._rb.get(self._seg_ns + key)
        except Exception as e:
            log.debug("seg cache get failed: %s", e)
            return None

    async def put_labels(self, key: str, png: bytes) -> None:
        try:
            await self._rb.set(self._seg_ns + key, png, ex=SEG_TTL_DAYS * 86400)
        except Exception as e:
            log.debug("seg cache put failed: %s", e)

    async def close(self) -> None:
        await self._r.aclose()
        await self._rb.aclose()
