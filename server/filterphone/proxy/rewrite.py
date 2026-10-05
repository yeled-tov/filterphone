"""נרמול בקשות תמונה לפני שהן יוצאות לאתר — כדי שהתשובה תגיע בפורמט שהמנוע בודק היטב.

הבעיה (אליאקספרס, ועוד אתרים רבים): שרת התמונות בוחר פורמט לפי כותרת Accept של
הדפדפן. Chrome מכריז על AVIF, ולכן הוא מקבל AVIF גם כשבכתובת כתוב ‎.jpg_.webp.
AVIF (ו-JPEG XL / HEIC) — פענוח איטי יותר, ותלוי בספרייה שמותקנת בשרת. אם הפענוח
נכשל, התמונה "לא ניתנת לבדיקה". כאן מבקשים מהאתר WebP/JPEG מראש:
  1. Accept בלי avif / jxl / heic / heif.
  2. כתובות שמבקשות פורמט במפורש: ‎_.avif של Alibaba (‎.jpg_.avif → ‎.jpg_.webp),
     ופרמטרים נפוצים של שירותי תמונות (format=avif, fm=avif, f_avif של Cloudinary).
הדפדפן מקבל WebP/JPEG במקום AVIF — מבחינתו זה שקוף (img מזהה פורמט לפי התוכן).
"""
from __future__ import annotations

import re

_HARD_FORMATS = ("image/avif", "image/jxl", "image/heic", "image/heif")
SAFE_IMAGE_ACCEPT = "image/webp,image/apng,image/*,*/*;q=0.8"

_ALI_HOSTS = re.compile(r"(?:^|\.)(?:alicdn\.com|aliexpress-media\.com|aliexpress\.com|alibaba\.com)$")
_ALI_SUFFIX = re.compile(r"_\.(?:avif|heic)$", re.IGNORECASE)
_QUERY_FORMAT = re.compile(r"(?:^|(?<=&))(format|fm|f|output)=(avif|jxl|heic)\b", re.IGNORECASE)
_CLOUDINARY = re.compile(r"(?<=[/,])f_(avif|jxl)(?=[,/])", re.IGNORECASE)


def image_accept(accept: str) -> str | None:
    """Accept חדש לבקשת תמונה, או None אם אין צורך לשנות."""
    a = accept.lower()
    if not any(f in a for f in _HARD_FORMATS):
        return None
    parts = [p.strip() for p in accept.split(",") if p.strip()]
    kept = [p for p in parts if p.split(";", 1)[0].strip().lower() not in _HARD_FORMATS]
    if not any(p.split(";", 1)[0].strip().lower() == "image/webp" for p in kept):
        kept.insert(0, "image/webp")
    return ",".join(kept) or SAFE_IMAGE_ACCEPT


def image_path(host: str, path: str) -> str | None:
    """נתיב חדש (כולל query) שמבקש פורמט בטוח, או None אם אין צורך לשנות."""
    base, sep, query = path.partition("?")
    new_base, new_query = base, query
    if _ALI_HOSTS.search(host.lower()):
        new_base = _ALI_SUFFIX.sub("_.webp", base)
    if query:
        new_query = _QUERY_FORMAT.sub(lambda m: f"{m.group(1)}=webp", query)
    new_base = _CLOUDINARY.sub("f_webp", new_base)
    if new_base == base and new_query == query:
        return None
    return new_base + (sep + new_query if sep else "")
