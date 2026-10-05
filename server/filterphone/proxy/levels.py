"""טבלת רמות הסינון לפי כתובת ה-IP של המכשיר ב-VPN.

ה-API כותב ל-Redis (hash בשם fp:levels: ip → רמה). הפרוקסי מחזיק עותק בזיכרון
ומרענן כל כמה שניות, כך שהבדיקה לכל בקשה היא קריאה ממילון — בלי רשת.
תאימות לאחור: גם המפתחות של המערכת הישנה (filterphone:vpn_ip:<ip>:filter_level).
"""
from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis

log = logging.getLogger("filterphone.levels")

LEVELS_KEY = "fp:levels"
_LEGACY_PATTERN = "filterphone:vpn_ip:*:filter_level"


class LevelTable:
    def __init__(self, redis: aioredis.Redis, default: int, refresh_s: float = 3.0):
        self._r = redis
        self._default = default
        self._refresh = refresh_s
        self._levels: dict[str, int] = {}

    def get(self, ip: str) -> int:
        return self._levels.get(ip, self._default)

    async def refresh(self) -> None:
        merged: dict[str, int] = {}
        async for key in self._r.scan_iter(match=_LEGACY_PATTERN, count=200):
            ip = key.split(":")[2]
            val = await self._r.get(key)
            if val is not None and val.lstrip("-").isdigit():
                merged[ip] = int(val)
        for ip, val in (await self._r.hgetall(LEVELS_KEY)).items():
            if val.lstrip("-").isdigit():
                merged[ip] = int(val)       # המערכת החדשה גוברת
        self._levels = merged

    async def run(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception as e:          # Redis לא זמין: נשארים עם הטבלה האחרונה
                log.warning("level refresh failed: %s", e)
            await asyncio.sleep(self._refresh)
