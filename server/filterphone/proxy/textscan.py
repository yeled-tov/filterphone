"""סינון טקסט בדפים (בכל רמות הגלישה): דף שהטקסט שלו מלא בתוכן לא ראוי — נחסם כולו.

משתמש באותה רשימת מונחים של חסימת מילות החיפוש (resolve_keywords → serp.TermMatcher), כך שאין רשימה
נוספת לתחזק. דף נחסם רק כשיש בו *הרבה* התאמות וגם צפיפות גבוהה — מילה אחת בכתבה לא חוסמת דף. טהור.
"""
from __future__ import annotations

import re

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
