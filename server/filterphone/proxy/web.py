"""מצב רמות הגלישה בפרוקסי: פסקי הדין לאתרים (מה שהבוט / המנהל קבעו), רשימת האתרים למבוגרים,
ותור הבדיקה של אתרים חדשים.

Redis (ה-API כותב, הפרוקסי קורא):
    fp:web:verdict  (hash)    אתר → פסק דין (websites.VERDICTS). נטען במלואו בעלייה.
    fp:web:changes  (stream)  כל שינוי בפסק דין — הפרוקסי קורא כל שנייה (לא טוען את כל הטבלה שוב).
    fp:web:check    (list)    אתרים שמחכים לבוט (הפרוקסי דוחף, ה-API שולף).
    fp:web:reviews  (stream)  "שליחה לבדיקה" מדף החסימה: כתובת המכשיר, אתר, קישור, הערה.
רשימת האתרים למבוגרים — קובץ שה-API מוריד (HaGeZi NSFW, דומיין בשורה), נטען כשהוא משתנה.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import redis.asyncio as aioredis

from ..websites import lookup, norm_host

log = logging.getLogger("filterphone.web")

VERDICT_KEY = "fp:web:verdict"
CHANGES_KEY = "fp:web:changes"
CHECK_KEY = "fp:web:check"
REVIEWS_KEY = "fp:web:reviews"
CHECK_WAIT_S = 45.0         # אתר שהבוט לא סיים לבדוק תוך זמן כזה — נחסם (עם "שליחה לבדיקה")
REQUEUE_S = 120.0           # לא דוחפים לתור שוב את אותו אתר בתוך זמן כזה
FULL_RELOAD_S = 900.0
LIST_CHECK_S = 600.0


def load_domain_list(path: str) -> frozenset[str]:
    """קובץ של דומיין בשורה (הערות ב-#/!). שגיאה — רשימה ריקה."""
    out: set[str] = set()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip().lower()
                if not line or line[0] in "#!":
                    continue
                if line.startswith("||"):
                    line = line[2:].split("^", 1)[0]
                line = line.removeprefix("*.").rstrip(".")
                if line and " " not in line:
                    out.add(line)
    except OSError:
        return frozenset()
    return frozenset(out)


class WebState:
    def __init__(self, redis: aioredis.Redis, adult_list_path: str = "", clock=time.monotonic):
        self._r = redis
        self._clock = clock
        self.verdicts: dict[str, str] = {}
        self.adult_hosts: frozenset[str] = frozenset()
        self._adult_path = adult_list_path
        self._adult_mtime = -1.0
        self._last_id = "0-0"
        self._queued: dict[str, float] = {}
        self._waiting: dict[str, float] = {}
        self._loaded_at = -1e18
        self._list_at = -1e18

    # ------------------------------------------------------------ שאלות

    def verdict(self, host: str) -> str | None:
        return lookup(self.verdicts, norm_host(host)) if self.verdicts else None

    def is_adult(self, host: str) -> bool:
        if not self.adult_hosts or not host:
            return False
        parts = host.lower().rstrip(".").split(".")
        return any(".".join(parts[i:]) in self.adult_hosts for i in range(len(parts) - 1))

    def waited(self, host: str) -> float:
        """כמה זמן (שניות) הלקוח כבר מחכה לבדיקה של האתר. מתחיל לספור בפעם הראשונה."""
        now = self._clock()
        host = norm_host(host)
        if len(self._waiting) > 5000:
            self._waiting.clear()
        first = self._waiting.setdefault(host, now)
        return now - first

    # ------------------------------------------------------------ פעולות

    def should_queue(self, host: str) -> bool:
        """האם לדחוף את האתר לתור (ולא כבר נדחף לאחרונה)."""
        host = norm_host(host)
        now = self._clock()
        last = self._queued.get(host)
        if last is not None and now - last < REQUEUE_S:
            return False
        if len(self._queued) > 5000:
            self._queued.clear()
        self._queued[host] = now
        return True

    async def queue_check(self, host: str) -> None:
        try:
            await self._r.lpush(CHECK_KEY, norm_host(host))
        except Exception as e:
            log.debug("queue check failed: %s", e)

    async def review(self, ip: str, host: str, url: str, note: str) -> None:
        await self._r.xadd(REVIEWS_KEY, {"ip": ip, "host": norm_host(host), "url": url[:500], "note": note[:300],
                                         "ts": int(time.time())}, maxlen=5000, approximate=True)

    def apply(self, host: str, verdict: str) -> None:
        host = norm_host(host)
        if verdict:
            self.verdicts[host] = verdict
        else:
            self.verdicts.pop(host, None)
        self._waiting.pop(host, None)
        self._queued.pop(host, None)

    # ------------------------------------------------------------ טעינה

    async def load(self) -> None:
        last = await self._r.xrevrange(CHANGES_KEY, count=1)
        self.verdicts = {norm_host(h): v for h, v in (await self._r.hgetall(VERDICT_KEY)).items() if v}
        self._last_id = last[0][0] if last else "0-0"
        self._loaded_at = self._clock()

    async def poll(self) -> int:
        """שינויים חדשים מה-API. מחזיר כמה הוחלו."""
        res = await self._r.xread({CHANGES_KEY: self._last_id}, count=2000)
        n = 0
        for _stream, entries in res or ():
            for entry_id, fields in entries:
                self._last_id = entry_id
                self.apply(fields.get("host", ""), fields.get("verdict", ""))
                n += 1
        return n

    def reload_list_if_changed(self) -> bool:
        if not self._adult_path:
            return False
        try:
            mtime = os.path.getmtime(self._adult_path)
        except OSError:
            return False
        if mtime == self._adult_mtime:
            return False
        self.adult_hosts = load_domain_list(self._adult_path)
        self._adult_mtime = mtime
        log.info("adult list loaded: %d domains", len(self.adult_hosts))
        return True

    async def run(self) -> None:
        while True:
            now = self._clock()
            try:
                if now - self._loaded_at >= FULL_RELOAD_S:
                    await self.load()
                else:
                    await self.poll()
            except Exception as e:
                log.debug("web state refresh failed: %s", e)
            if now - self._list_at >= LIST_CHECK_S:
                self._list_at = now
                await asyncio.to_thread(self.reload_list_if_changed)
            await asyncio.sleep(1.0)
