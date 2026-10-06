"""לקוח למנוע הסינון: מאגר חיבורים קבועים ל-Unix socket, עם מגבלת זמן כוללת ו"מפסק".

* מגבלת זמן אחת לכל הבקשה — כולל ההמתנה לחיבור פנוי. המנוע מקבל את הזמן שנשאר
  (budget_ms) ומפסיק לעבוד על בקשה שהפרוקסי כבר לא מחכה לה.
* חיבור רדום שהמנוע סגר (למשל אחרי הפעלה מחדש) — מנסים שוב פעם אחת בחיבור חדש.
* מפסק (circuit breaker): אחרי כמה כשלונות רצופים לא מנסים להתחבר לזמן קצר (שהולך וגדל),
  וכל תמונה מקבלת מיד את מדיניות הכשל (רמות 1–2: חסימה). כך מנוע שנפל לא גורם לכל
  תמונה לחכות 6 שניות, ולא נוצר גל חיבורים כשהוא עולה.
"""
from __future__ import annotations

import asyncio
import time

from .. import protocol

Conn = tuple[asyncio.StreamReader, asyncio.StreamWriter]
BUDGET_MARGIN_MS = 400


class EngineUnavailable(Exception):
    """המפסק פתוח — לא מנסים בכלל."""


class CircuitBreaker:
    """סגור → (threshold כשלים רצופים) → פתוח לזמן קצר → ניסיון אחד → סגור / פתוח לזמן כפול.

    כשל = המנוע לא עונה (חיבור נדחה / נסגר / תקוע). המתנה בתור של הפרוקסי עצמו (עומס) — לא כשל
    של המנוע, ולא פותחת את המפסק. כשלים של בקשות שכבר היו בדרך כשהמפסק נפתח — לא מאריכים אותו.
    """

    def __init__(self, threshold: int = 5, base_s: float = 2.0, max_s: float = 30.0,
                 clock=time.monotonic):
        self.threshold = threshold
        self.base = base_s
        self.max = max_s
        self._clock = clock
        self.failures = 0
        self.opened = 0              # כמה פעמים נפתח ברצף (להשהיה הולכת וגדלה)
        self.open_until = 0.0
        self._state = "closed"       # closed / open / half_open
        self._probing = False

    @property
    def is_open(self) -> bool:
        return self._state != "closed" and self._clock() < self.open_until

    def allow(self) -> bool:
        if self._state == "closed":
            return True
        if self._clock() < self.open_until or self._probing:
            return False
        self._state = "half_open"
        self._probing = True
        return True

    def success(self) -> None:
        self.failures = 0
        self.opened = 0
        self._probing = False
        self._state = "closed"
        self.open_until = 0.0

    def abandon(self) -> None:
        """הבקשה לא הגיעה למנוע (פג זמנה בתור של הפרוקסי) — לא הצלחה ולא כשל."""
        self._probing = False

    def _open(self) -> None:
        self.open_until = self._clock() + min(self.max, self.base * (2 ** self.opened))
        self.opened += 1
        self._state = "open"

    def failure(self) -> None:
        if self._state == "half_open":
            self._probing = False
            self._open()                # הניסיון נכשל — פתוח שוב, לזמן כפול
        elif self._state == "closed":
            self.failures += 1
            if self.failures >= self.threshold:
                self._open()
        # open: כשל של בקשה ישנה שהייתה בדרך — לא מאריך


class EngineClient:
    def __init__(self, path: str, pool_size: int, timeout_s: float, breaker: CircuitBreaker | None = None):
        self._path = path
        self._timeout = timeout_s
        self._sem = asyncio.Semaphore(pool_size)
        self._idle: list[Conn] = []
        self.breaker = breaker or CircuitBreaker()
        self._last_ok = time.monotonic()

    async def classify(self, body: bytes, header: dict) -> tuple[dict, bytes]:
        if not self.breaker.allow():
            raise EngineUnavailable("engine circuit open")
        deadline = asyncio.get_running_loop().time() + self._timeout
        reached = False
        try:
            async with asyncio.timeout_at(deadline):
                async with self._sem:
                    reached = True
                    result = await self._call(body, header, deadline)
        except TimeoutError:
            if reached and time.monotonic() - self._last_ok > self._timeout:
                self.breaker.failure()      # המנוע לא ענה לאף בקשה כל הזמן הזה — תקוע
            else:
                self.breaker.abandon()      # עומס (אצלנו או במנוע) — לא תקלה
            raise
        except (OSError, EOFError, protocol.ProtocolError):
            self.breaker.failure()
            raise
        self._last_ok = time.monotonic()
        self.breaker.success()
        return result

    async def _call(self, body: bytes, header: dict, deadline: float) -> tuple[dict, bytes]:
        loop = asyncio.get_running_loop()
        for attempt in (0, 1):
            reused = attempt == 0 and bool(self._idle)
            conn = self._idle.pop() if reused else await asyncio.open_unix_connection(self._path)
            # המנוע מקבל קצת פחות זמן מאיתנו — כדי שהתשובה שלו ("לא הספקתי") תגיע לפני שנוותר
            budget = {**header, "budget_ms": max(1, int((deadline - loop.time()) * 1000) - BUDGET_MARGIN_MS)}
            try:
                result = await self._roundtrip(conn, budget, body)
            except (ConnectionError, asyncio.IncompleteReadError):
                conn[1].close()
                if reused:
                    # חיבור ישן שהמנוע סגר (הפעלה מחדש) — גם שאר החיבורים הרדומים מאותה תקופה.
                    # ניסיון אחד נוסף בחיבור חדש.
                    self.close_idle()
                    continue
                raise
            except BaseException:
                conn[1].close()         # חיבור במצב לא ידוע — לא מחזירים למאגר
                raise
            self._idle.append(conn)
            return result
        raise ConnectionError("engine connection failed")

    def close_idle(self) -> None:
        while self._idle:
            self._idle.pop()[1].close()

    @staticmethod
    async def _roundtrip(conn: Conn, header: dict, body: bytes) -> tuple[dict, bytes]:
        reader, writer = conn
        writer.write(protocol.encode(header, body))
        await writer.drain()
        return await protocol.read_message(reader)
