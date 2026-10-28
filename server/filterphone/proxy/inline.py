"""תמונות "מוטמעות" בתוך דף: data:image/...;base64,... — בעיקר בגוגל.

גוגל שולחת את התמונות הממוזערות של דף הבית ושל תוצאות החיפוש (חדשות, תמונות,
"סיפורים מובילים", השלמה אוטומטית עם תמונה) בתוך ה-HTML / JavaScript / JSON עצמו,
כטקסט base64 — לא כבקשת תמונה נפרדת. הפרוקסי בדק רק תשובות image/*, ולכן לא ראה
אותן בכלל. כאן מוצאים אותן בתוך תשובות טקסט, שולחים כל אחת למנוע, ומחליפים
במקום — באותו סגנון כתיבה (escape) של המקור, כדי שה-JS / ה-JSON יישארו תקינים.

עובדים על בייטים (אחרי פריסת gzip/brotli) ולא על טקסט: אין תלות בקידוד הדף,
וכל מה שמחוץ לתמונות נשאר זהה בייט-בייט.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# אותו תו יכול להופיע "בורח" בתוך מחרוזת JS / JSON: \/  \x2f  /  וכו'
_SLASH = rb"(?:/|\\/|\\x2[fF]|\\u002[fF])"
_SEMI = rb"(?:;|\\x3[bB]|\\u003[bB])"
_B64 = rb"(?:[A-Za-z0-9+/=]|\\/|\\x(?:3[dD]|2[bB]|2[fF])|\\u00(?:3[dD]|2[bB]|2[fF]))"
MIN_B64_CHARS = 400             # ~300 בייט: מתחת לזה פיקסלים ואייקונים זעירים (כמו min_image_bytes)

DATA_URI = re.compile(
    rb"data:image(?P<slash>" + _SLASH + rb")(?P<fmt>jpeg|jpg|png|webp|gif|avif|bmp)"
    rb"(?P<semi>" + _SEMI + rb")base64,(?P<b64>" + _B64 + rb"{%d,})" % MIN_B64_CHARS)

_UNESCAPE = re.compile(rb"\\/|\\x(?:3[dD]|2[bB]|2[fF])|\\u00(?:3[dD]|2[bB]|2[fF])")
_PLAIN = {b"3d": b"=", b"2b": b"+", b"2f": b"/"}

TEXT_TYPES = ("text/html", "application/xhtml+xml", "application/json", "text/javascript",
              "application/javascript", "application/x-javascript", "text/plain", "text/css",
              "application/json+protobuf")

# אתרים ששולחים תמונות מוטמעות גם בתשובות JSON / JS (לא רק ב-HTML): מנועי חיפוש, וחנויות
# שמחזירות לפעמים תמונות ממוזערות בתוך ה-API שלהן
_INLINE_HOSTS = re.compile(r"(?:^|\.)(?:google\.[a-z.]{2,6}|bing\.com|yahoo\.com|duckduckgo\.com|"
                           r"yandex\.[a-z.]{2,6}|ecosia\.org|startpage\.com|aliexpress\.[a-z.]{2,6}|"
                           r"shein\.[a-z.]{2,6}|temu\.com)$")


def should_scan(content_type: str, host: str) -> bool:
    ct = content_type.split(";", 1)[0].strip().lower()
    if ct in ("text/html", "application/xhtml+xml"):
        return True
    return ct in TEXT_TYPES and bool(_INLINE_HOSTS.search(host.lower().rstrip(".")))


def _unescape(raw: bytes) -> bytes:
    def one(m: re.Match) -> bytes:
        t = m.group(0)
        return b"/" if t == rb"\/" else _PLAIN[t[-2:].lower()]
    return _UNESCAPE.sub(one, raw)


def decode_payload(raw: bytes) -> bytes | None:
    """הטקסט שאחרי base64, → בייטים של התמונה (None אם לא תקין)."""
    data = _unescape(raw).rstrip(b"=")
    try:
        return base64.b64decode(data + b"=" * (-len(data) % 4), validate=True)
    except (binascii.Error, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class _Style:
    """איך המקור כתב את התווים המיוחדים — כדי לכתוב את התחליף באותו אופן."""
    slash: bytes
    eq: bytes
    plus: bytes

    @classmethod
    def of(cls, m: re.Match) -> _Style:
        b64 = m.group("b64")
        def pick(plain: bytes, code: bytes) -> bytes:
            for esc in (rb"\x" + code, rb"\x" + code.upper(), rb"\u00" + code, rb"\u00" + code.upper()):
                if esc in b64:
                    return esc
            return plain
        slash = rb"\/" if rb"\/" in b64 else pick(b"/", b"2f")
        if slash == b"/" and m.group("slash") != b"/":
            slash = m.group("slash")            # מהקידומת image\/jpeg
        return cls(slash, pick(b"=", b"3d"), pick(b"+", b"2b"))

    def encode(self, data: bytes) -> bytes:
        out = base64.b64encode(data)
        if self.slash != b"/":
            out = out.replace(b"/", self.slash)
        if self.eq != b"=":
            out = out.replace(b"=", self.eq)
        if self.plus != b"+":
            out = out.replace(b"+", self.plus)
        return out


# מה המנוע מחזיר לכל תמונה: None = להשאיר; אחרת (בייטים חדשים, סוג תוכן)
Judge = Callable[[bytes], Awaitable[tuple[bytes, str] | None]]


@dataclass(slots=True)
class Result:
    body: bytes
    found: int = 0
    replaced: int = 0


async def rewrite(body: bytes, judge: Judge, min_bytes: int, max_images: int = 120,
                  overflow: Callable[[bytes], tuple[bytes, str] | None] | None = None,
                  concurrency: int = 6) -> Result:
    """מוצא תמונות מוטמעות, שולח למנוע, ומחזיר את הגוף עם התחליפים.

    מעבר ל-max_images (דף חריג) — לא שולחים למנוע אלא ל-overflow (למשל: חסימה ברמות
    המחמירות), כדי שדף ענק לא יתקע את המנוע וגם לא יעבור בלי בדיקה.
    """
    matches = list(DATA_URI.finditer(body))
    if not matches:
        return Result(body)
    sem = asyncio.Semaphore(concurrency)

    async def one(i: int, m: re.Match) -> tuple[bytes, str] | None:
        img = decode_payload(m.group("b64"))
        if img is None or len(img) < min_bytes:
            return None
        if i >= max_images:
            return overflow(img) if overflow else None
        async with sem:
            return await judge(img)

    results = await asyncio.gather(*(one(i, m) for i, m in enumerate(matches)))
    out, pos, replaced = [], 0, 0
    for m, r in zip(matches, results, strict=True):
        if r is None:
            continue
        new_bytes, ct = r
        fmt = ct.split("/", 1)[-1].split(";", 1)[0].strip().encode() or b"png"
        style = _Style.of(m)
        out += [body[pos:m.start()], b"data:image", m.group("slash"), fmt, m.group("semi"), b"base64,",
                style.encode(new_bytes)]
        pos = m.end()
        replaced += 1
    out.append(body[pos:])
    return Result(b"".join(out), len(matches), replaced)
