"""אילו חיבורי HTTPS לפתוח לבדיקה ואילו להעביר כמו שהם (passthrough).

שלושה מקורות:
  1. רשימה קבועה (passthrough.txt): אפליקציות עם certificate pinning — בנקים,
     וואטסאפ, עדכוני מערכת. יירוט ישבור אותן, ואין בהן תמונות לסנן.
  2. למידה: אפליקציה שדחתה את התעודה שלנו (באנדרואיד רוב האפליקציות לא סומכות
     על תעודה שהמשתמש התקין) — החיבורים הבאים מאותו מכשיר לאותו שרת עוברים
     ישירות, כדי שהאפליקציה תעבוד.
  3. "תמיד לבדוק" (פרק [inspect] בקובץ): שרתי תמונות של רשתות חברתיות, חנויות וגוגל.
     במכשיר במצב "תמונות באפליקציות: חסום" (ברירת המחדל ברמות 1–2) לא לומדים עליהם —
     עדיף שהאפליקציה לא תציג תמונות מאשר שתציג בלי סינון. באותו מצב גם שרת ששמו
     מעיד על תמונות (img., images-, ‎-pic-, media, thumbs...) לא נלמד.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

_DISTRUST_MARKERS = ("does not trust", "certificate unknown", "unknown ca", "bad certificate",
                     "certificate_unknown", "unknown_ca", "bad_certificate")
# שם שרת שמעיד שהוא מגיש תמונות: img.kwcdn.com, ae-pic-a1.aliexpress-media.com, images-na...
_IMAGE_HOST = re.compile(r"(?:^|[.-])(?:img|imgs|image|images|pic|pics|photo|photos|thumb|thumbs|"
                         r"thumbnail|thumbnails|avatar|avatars|media)\d*(?:[.-]|$)")


def _glob(p: str) -> str:
    """'*.example.com' תופס גם את example.com עצמו וגם כל תת-דומיין. '*' בשאר המקומות = כל רצף."""
    p = p.lower()
    if p.startswith("*."):
        return r"(?:.*\.)?" + re.escape(p[2:]).replace(r"\*", ".*")
    return re.escape(p).replace(r"\*", ".*")


def _compile(patterns: list[str]) -> re.Pattern | None:
    if not patterns:
        return None
    return re.compile(rf"^(?:{'|'.join(_glob(p) for p in patterns)})$")


def load_lists(path: str | Path) -> tuple[list[str], list[str]]:
    """מחזיר (passthrough, inspect) מתוך קובץ עם פרקים [passthrough] ו-[inspect]."""
    sections: dict[str, list[str]] = {"passthrough": [], "inspect": []}
    current = "passthrough"
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().lower()
            sections.setdefault(current, [])
            continue
        sections[current].append(line)
    return sections["passthrough"], sections["inspect"]


class HostPolicy:
    def __init__(self, passthrough: list[str], inspect: list[str], learn_ttl_s: float):
        self._static = _compile(passthrough)
        self._inspect = _compile(inspect)
        self._ttl = learn_ttl_s
        self._learned: dict[tuple[str, str], float] = {}     # (ip, host) → תפוגה

    def is_static(self, host: str) -> bool:
        return bool(self._static and self._static.match(host))

    def is_inspect_only(self, host: str) -> bool:
        return bool(self._inspect and self._inspect.match(host))

    @staticmethod
    def looks_like_image_host(host: str) -> bool:
        return bool(_IMAGE_HOST.search(host))

    def should_passthrough(self, client_ip: str, host: str | None) -> bool:
        if not host:
            return True                     # בלי SNI אין לנו איך להנפיק תעודה מתאימה
        host = host.lower().rstrip(".")
        # [inspect] גובר על [passthrough]: שרת תמונות שרשום במפורש ב-[inspect] נבדק תמיד, גם אם הוא
        # נתפס ע"י glob רחב ב-[passthrough] (למשל streetviewpixels-pa.googleapis.com מול *.googleapis.com).
        if self.is_static(host) and not self.is_inspect_only(host):
            return True
        exp = self._learned.get((client_ip, host))
        if exp is None:
            return False
        if exp < time.time():
            del self._learned[(client_ip, host)]
            return False
        return True

    @staticmethod
    def is_distrust_error(error: str | None) -> bool:
        e = (error or "").lower()
        return any(m in e for m in _DISTRUST_MARKERS)

    def learn(self, client_ip: str, host: str, strict_images: bool,
              expires_at: float | None = None) -> bool:
        """מסמן ש-(מכשיר, שרת) לא סומך עלינו. מחזיר False אם בכוונה לא לומדים.

        strict_images: המכשיר במצב "תמונות באפליקציות: חסום" — שרתי תמונות לא נלמדים.
        """
        host = host.lower().rstrip(".")
        if strict_images and (self.is_inspect_only(host) or self.looks_like_image_host(host)):
            return False
        self._learned[(client_ip, host)] = expires_at or (time.time() + self._ttl)
        return True

    def replace_learned(self, learned: dict[tuple[str, str], float]) -> None:
        """מחליף את כל הרשימה הנלמדת (טעינה מחדש מ-Redis — Redis הוא מקור האמת)."""
        self._learned = dict(learned)

    def forget_device(self, client_ip: str) -> None:
        for k in [k for k in self._learned if k[0] == client_ip]:
            del self._learned[k]

    def learned_count(self) -> int:
        return len(self._learned)
