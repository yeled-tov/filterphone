"""מדיניות לכל מכשיר, לפי כתובת ה-IP שלו ב-VPN: רמת סינון + חסימות שהמנהל הפעיל.

ה-API כותב ל-Redis:
    fp:policy      (hash)    ip → JSON  {"level": 2, "hosts": [...], "images": "filter", "video": false,
                                         "app_images": "block"}
    fp:levels      (hash)    ip → רמה   (תאימות לאחור — גרסאות קודמות של ה-API)
    fp:policy:rev  (מספר)    עולה בכל שינוי. הפרוקסי בודק אותו כל 3 שניות (פקודה זולה אחת),
                             וקורא את הטבלאות רק כשהוא השתנה — או פעם בדקה, ליתר ביטחון.
הפרוקסי מחזיק עותק בזיכרון — הבדיקה לכל בקשה היא קריאה ממילון.
תאימות לאחור: גם המפתחות של המערכת הישנה (filterphone:vpn_ip:<ip>:filter_level). סריקה שלהם
עוברת על כל המפתחות ב-Redis (גם קאש התמונות) — לכן רק בעלייה ופעם ב-10 דקות.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

import redis.asyncio as aioredis

from ..websites import WebPolicy

log = logging.getLogger("filterphone.policy")

POLICY_KEY = "fp:policy"
LEVELS_KEY = "fp:levels"
POLICY_REV_KEY = "fp:policy:rev"
_LEGACY_PATTERN = "filterphone:vpn_ip:*:filter_level"
FULL_RELOAD_S = 60.0
LEGACY_SCAN_S = 600.0


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
    paths: tuple[tuple[re.Pattern, re.Pattern], ...] = ()   # (אתר, נתיב) — למשל לשונית התמונות בגוגל
    host_list: tuple[str, ...] = field(default=(), compare=False)
    web: WebPolicy = field(default_factory=WebPolicy)       # רמת גלישה (websites.py) — רק על ניווט לדפים

    @property
    def filtering(self) -> bool:
        """האם יש בכלל מה לבדוק במכשיר הזה (אחרת — הכול עובר ישר, בלי יירוט)."""
        return self.level > 0 or self.images == "block" or self.video or self.web.active

    def blocks(self, host: str | None) -> bool:
        return bool(host and self.hosts and self.hosts.match(host.lower().rstrip(".")))

    def blocks_path(self, host: str, path: str) -> bool:
        host = host.lower().rstrip(".")
        return any(h.match(host) and p.search(path) for h, p in self.paths)

    @classmethod
    def from_json(cls, raw: str, default_level: int) -> DevicePolicy:
        d = json.loads(raw)
        hosts = [str(h) for h in d.get("hosts", [])][:5000]
        level = int(d.get("level", default_level))
        paths = []
        for rule in d.get("paths", [])[:50]:
            h = compile_hosts([str(rule["host"])])
            if h is not None:
                paths.append((h, re.compile(str(rule["re"]))))
        return cls(
            level=level,
            hosts=compile_hosts(hosts),
            paths=tuple(paths),
            images="block" if d.get("images") == "block" else "filter",
            video=bool(d.get("video", False)),
            app_images=str(d.get("app_images") or default_app_images(level)),
            host_list=tuple(hosts),
            web=WebPolicy.from_json(d.get("web")),
        )


def default_app_images(level: int) -> str:
    """ברירת מחדל: ברמות 1–2 תמונות באפליקציות שאי אפשר לסנן — לא נטענות."""
    return "allow" if level >= 3 or level <= 0 else "block"


class PolicyTable:
    def __init__(self, redis: aioredis.Redis, default_level: int, refresh_s: float = 3.0,
                 clock=time.monotonic):
        self._r = redis
        self._default = DevicePolicy(default_level, app_images=default_app_images(default_level))
        self._default_level = default_level
        self._refresh = refresh_s
        self._clock = clock
        self._policies: dict[str, DevicePolicy] = {}
        self._raw: dict[str, str] = {}
        self._legacy: dict[str, DevicePolicy] = {}
        self._rev: str | None = None
        self._loaded_at = -1e18
        self._legacy_at = -1e18
        self.reloads = 0

    def get(self, ip: str) -> DevicePolicy:
        return self._policies.get(ip, self._default)

    def level(self, ip: str) -> int:
        return self.get(ip).level

    def set(self, ip: str, policy: DevicePolicy) -> None:
        """לבדיקות."""
        self._policies[ip] = policy

    def __len__(self) -> int:
        return len(self._policies)

    async def _scan_legacy(self) -> dict[str, DevicePolicy]:
        out: dict[str, DevicePolicy] = {}
        async for key in self._r.scan_iter(match=_LEGACY_PATTERN, count=1000):
            ip = key.split(":")[2]
            val = await self._r.get(key)
            if val is not None and val.lstrip("-").isdigit():
                out[ip] = DevicePolicy(int(val), app_images=default_app_images(int(val)))
        return out

    async def refresh(self, force: bool = False) -> bool:
        """טוען מחדש אם משהו השתנה. מחזיר True אם הטבלה נטענה."""
        now = self._clock()
        rev = await self._r.get(POLICY_REV_KEY)
        if not force and rev == self._rev and now - self._loaded_at < FULL_RELOAD_S:
            return False
        if force or now - self._legacy_at >= LEGACY_SCAN_S:
            self._legacy = await self._scan_legacy()
            self._legacy_at = now
        merged: dict[str, DevicePolicy] = dict(self._legacy)
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
            except (ValueError, TypeError, KeyError, re.error) as e:
                log.warning("bad policy for %s: %s", ip, e)
        self._raw = dict(raw_all)
        self._policies = merged
        self._rev = rev
        self._loaded_at = now
        self.reloads += 1
        return True

    async def run(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception as e:          # Redis לא זמין: נשארים עם הטבלה האחרונה
                log.warning("policy refresh failed: %s", e)
            await asyncio.sleep(self._refresh)
