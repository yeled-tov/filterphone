"""סינון טקסט בדפים (בכל רמות הגלישה): דף שהטקסט שלו מלא בתוכן לא ראוי — נחסם כולו; בדף רגיל (כתבה)
המילים הבעייתיות מכוסות ב-XXXX, וקטע שיש בו כמה כאלה — מכוסה כולו (כמו כתוביות מסוננות בסרט).

משתמש באותה רשימת מונחים של חסימת מילות החיפוש (resolve_keywords → serp.TermMatcher), כך שאין רשימה
נוספת לתחזק. דף נחסם רק כשיש בו *הרבה* התאמות וגם צפיפות גבוהה — מילה אחת בכתבה לא חוסמת דף. טהור.
"""
from __future__ import annotations

import re
from urllib.parse import unquote_plus

MAX_TEXT_CHARS = 400_000     # מעבר לזה לא קוראים (דפים ענקיים) — מספיק כדי לזהות דף בעייתי
MIN_HITS = 8                 # לפחות כך הרבה התאמות בדף
MIN_PER_1000_WORDS = 3.0     # וגם צפיפות: התאמות לכל 1000 מילים

_DROP = re.compile(r"<(script|style|noscript|svg|template)\b.*?</\1\s*>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_ENT = re.compile(r"&[#a-zA-Z0-9]+;")
_WS = re.compile(r"\s+")


def visible_text(html: bytes) -> str:
    """הטקסט שהגולש רואה (בלי סקריפטים, עיצוב ותגיות)."""
    s = html[: MAX_TEXT_CHARS * 2].decode("utf-8", "ignore")
    s = _DROP.sub(" ", s)
    s = _TAG.sub(" ", s)
    s = _ENT.sub(" ", s)
    return _WS.sub(" ", s)[:MAX_TEXT_CHARS]


def is_bad(text: str, terms) -> bool:
    """הרבה התאמות + צפיפות גבוהה → דף לא ראוי. [terms] — serp.TermMatcher."""
    hits = terms.count(text)
    if hits < MIN_HITS:
        return False
    words = max(1, len(text.split()))
    return hits * 1000 / words >= MIN_PER_1000_WORDS


def exempt(host: str) -> bool:
    """אתרים חיוניים שלא נחסמים על טקסט (משרדי ממשלה — מידע רפואי / חוקי)."""
    h = host.lower()
    return h.endswith(".gov.il") or h == "gov.il"


_URL_SEP = re.compile(r"[/_\-+.?=&#%:;,]+")


def url_words(path: str) -> str:
    """הנתיב והשאילתה של כתובת כטקסט (מפוענח, מפרידים → רווחים), כדי לבדוק מילים חסומות בכתובת."""
    return _URL_SEP.sub(" ", unquote_plus(path or "")[:2000])


# ---------------------------------------------------------------- כיסוי ב-XXXX

_SEGMENTS = re.compile(r"(<!--.*?-->|<[^>]*>)", re.S)
_SKIP_OPEN = re.compile(r"<(script|style|noscript|svg|template|textarea|code|pre)\b", re.I)
PASSAGE_HITS = 2                # קטע (צומת טקסט) עם לפחות כך הרבה התאמות...
PASSAGE_MAX_WORDS = 120         # ...וקצר מזה — מכוסה כולו. ארוך יותר — רק המילים עצמן
_LETTER = re.compile(r"[^\s\d.,:;!?()\[\]\"'״׳\-–—/]")


def _cover(text: str) -> str:
    """כל אות → X (רווחים, ספרות וסימני פיסוק נשארים — כדי שהעימוד לא יישבר)."""
    return _LETTER.sub("X", text)


def mask_html(page: str, terms) -> tuple[str, int]:
    """מכסה בדף HTML את המילים הבעייתיות ב-XXXX; קטע קצר עם כמה מהן — מכוסה כולו. רק בטקסט שמוצג (לא
    בתגיות, סקריפטים, עיצוב או שדות קלט). → (הדף אחרי הכיסוי, כמה התאמות כוסו). [terms] — serp.TermMatcher."""
    out: list[str] = []
    hits = 0
    skip: str | None = None
    for seg in _SEGMENTS.split(page):
        if not seg:
            continue
        if seg[0] == "<":
            if skip is None:
                m = _SKIP_OPEN.match(seg)
                if m and not seg.endswith("/>"):
                    skip = m.group(1).lower()
            elif re.match(rf"</{skip}\s*>", seg, re.I):
                skip = None
            out.append(seg)
            continue
        if skip is not None or not seg.strip():
            out.append(seg)
            continue
        spans = terms.spans(seg)
        if not spans:
            out.append(seg)
            continue
        hits += len(spans)
        if len(spans) >= PASSAGE_HITS and len(seg.split()) <= PASSAGE_MAX_WORDS:
            out.append(_cover(seg))
            continue
        buf, last = [], 0
        for a, b in spans:
            buf.append(seg[last:a])
            buf.append(_cover(seg[a:b]))
            last = b
        buf.append(seg[last:])
        out.append("".join(buf))
    return "".join(out), hits
