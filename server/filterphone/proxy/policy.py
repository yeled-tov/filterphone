"""מדיניות לכל מכשיר, לפי כתובת ה-IP שלו ב-VPN: רמת סינון + חסימות שהמנהל הפעיל.

ה-API כותב ל-Redis:
    fp:policy  (hash)  ip → JSON  {"level": 2, "hosts": [...], "images": "filter", "video": false,
                                   "app_images": "block"}
    fp:levels  (hash)  ip → רמה   (תאימות לאחור — גרסאות קודמות של ה-API)
הפרוקסי מחזיק עותק בזיכרון ומרענן כל כמה שניות — הבדיקה לכל בקשה היא קריאה ממילון.
תאימות לאחור: גם המפתחות של המערכת הישנה (filterphone:vpn_ip:<ip>:filter_level).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

import redis.asyncio as aioredis

log = logging.getLogger("filterphone.policy")

POLICY_KEY = "fp:policy"
LEVELS_KEY = "fp:levels"
_LEGACY_PATTERN = "filterphone:vpn_ip:*:filter_level"


def compile_hosts(patterns: list[str]) -> re.Pattern | None:
    """'example.com' חוסם גם כל תת-דומיין (כמו ‎||example.com^ ב-AdGuard). '*' = כל רצף."""
    parts = []
    for p in patterns:
        p = p.strip().lower().rstrip(".")
        if p.startswith("*."):
            p = p[2:]
        if p:
            parts.append(re.escape(p).replace(r"\*", "[a-z0-9.-]*"))
    if not parts:
        return None
    return re.compile(rf"^(?:[a-z0-9-]+\.)*(?:{'|'.join(parts)})$")


@dataclass(frozen=True, slots=True)
class DevicePolicy:
    level: int
    hosts: re.Pattern | None = None         # אתרים/שירותים חסומים למכשיר הזה
    images: str = "filter"                  # filter = לפי הרמה; block = כל התמונות נחסמות
    video: bool = False                     # חסימת וידאו (לפי סוג התוכן)
    app_images: str = "block"               # block = שרתי תמונות של אפליקציות לא עוברים בלי סינון
    host_list: tuple[str, ...] = field(default=(), compare=False)

    @property
    def filtering(self) -> bool:
        """האם יש בכלל מה לבדוק במכשיר הזה (אחרת — הכול עובר ישר, בלי יירוט)."""
        return self.level > 0 or self.images == "block" or self.video

    def blocks(self, host: str | None) -> bool:
        return bool(host and self.hosts and self.hosts.match(host.lower().rstrip(".")))

    @classmethod
    def from_json(cls, raw: str, default_level: int) -> DevicePolicy:
        d = json.loads(raw)
        hosts = [str(h) for h in d.get("hosts", [])][:5000]
        level = int(d.get("level", default_level))
        return cls(
            level=level,
            hosts=compile_hosts(hosts),
            images="block" if d.get("images") == "block" else "filter",
            video=bool(d.get("video", False)),
            app_images=str(d.get("app_images") or default_app_images(level)),
            host_list=tuple(hosts),
        )


def default_app_images(level: int) -> str:
    """ברירת מחדל: ברמות 1–2 תמונות באפליקציות שאי אפשר לסנן — לא נטענות."""
    return "allow" if level >= 3 or level <= 0 else "block"


class PolicyTable:
    def __init__(self, redis: aioredis.Redis, default_level: int, refresh_s: float = 3.0):
        self._r = redis
        self._default = DevicePolicy(default_level, app_images=default_app_images(default_level))
        self._default_level = default_level
        self._refresh = refresh_s
        self._policies: dict[str, DevicePolicy] = {}
        self._raw: dict[str, str] = {}

    def get(self, ip: str) -> DevicePolicy:
        return self._policies.get(ip, self._default)

    def level(self, ip: str) -> int:
        return self.get(ip).level

    def set(self, ip: str, policy: DevicePolicy) -> None:
        """לבדיקות."""
        self._policies[ip] = policy

    async def refresh(self) -> None:
        merged: dict[str, DevicePolicy] = {}
        async for key in self._r.scan_iter(match=_LEGACY_PATTERN, count=200):
            ip = key.split(":")[2]
            val = await self._r.get(key)
            if val is not None and val.lstrip("-").isdigit():
                merged[ip] = DevicePolicy(int(val), app_images=default_app_images(int(val)))
        for ip, val in (await self._r.hgetall(LEVELS_KEY)).items():
            if val.lstrip("-").isdigit():
                merged[ip] = DevicePolicy(int(val), app_images=default_app_images(int(val)))
        raw_all = await self._r.hgetall(POLICY_KEY)
        for ip, raw in raw_all.items():
            old = self._policies.get(ip)
            if self._raw.get(ip) == raw and old is not None:
                merged[ip] = old                # לא השתנה — בלי קומפילציה מחדש
                continue
            try:
                merged[ip] = DevicePolicy.from_json(raw, self._default_level)
            except (ValueError, TypeError, re.error) as e:
                log.warning("bad policy for %s: %s", ip, e)
        self._raw = dict(raw_all)
        self._policies = merged

    async def run(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception as e:          # Redis לא זמין: נשארים עם הטבלה האחרונה
                log.warning("policy refresh failed: %s", e)
            await asyncio.sleep(self._refresh)
