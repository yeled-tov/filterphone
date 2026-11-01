"""הרשאות לאנשי הצוות: לכל אזור בדשבורד — בלי גישה / צפייה / עריכה. בעלים — הכול תמיד.

האכיפה בשרת, לפי נתיב ה-API והפעולה (GET = צפייה; כל השאר = עריכה), כך שגם מי שעוקף את התצוגה לא מקבל
יותר ממה שהבעלים בחר. נתיב שלא מופיע כאן — רק לבעלים (נכשל סגור).
"""
from __future__ import annotations

import json

# הסדר — כמו בתפריט. התיאור מוצג בעמוד הצוות.
AREAS: dict[str, str] = {
    "requests": "בקשות הצטרפות ושינוי (אישור / דחייה)",
    "support": "תמיכה — הצ'אט עם הלקוחות",
    "devices": "מכשירים ומחשבים — מצב, חסימות, השהיה, חסימות אחרונות",
    "customers": "לקוחות — פרטים, תשלום, קוד הורים",
    "profiles": "פרופילים מוכנים",
    "web": "אתרים ורמות גלישה (כולל בקשות לבדיקת אתר)",
    "apps": "חנות האפליקציות",
    "wabot": "בוט הוואטסאפ והגדרות וואטסאפ",
    "codes": "קודי הפעלה והקמה ב-QR",
    "test": "בדיקת תמונה",
    "audit": "יומן פעולות",
    "system": "מערכת והגדרות (שרת, GPU, תעודות, מיתוג)",
}
LEVELS = ("view", "edit")

# לכל איש צוות — בלי קשר להרשאות (הכניסה, הסקירה, ההתראות, הסיסמה שלו)
_ALWAYS = {"catalog", "overview", "notifications", "team", "me", "logout", "login", "levels", "stats"}
_AREA_OF = {
    "requests": "requests", "swaps": "requests", "support": "support",
    "devices": "devices", "events": "devices", "fleet": "devices",
    "customers": "customers", "profiles": "profiles", "web": "web", "apps": "apps", "play": "apps",
    "wabot": "wabot", "whatsapp": "wabot", "codes": "codes", "provisioning": "codes", "test-image": "test",
    "audit": "audit", "system": "system", "branding": "system", "gpu": "system", "kosher-certs": "system",
    "adguard": "system",
}

PRESETS: dict[str, dict[str, str]] = {
    "support": {"support": "edit", "requests": "view", "devices": "view", "customers": "view"},
    "helper": {"support": "edit", "requests": "edit", "devices": "edit", "customers": "edit", "web": "edit",
               "profiles": "view", "apps": "view"},
    "all": {a: "edit" for a in AREAS},
}


def clean(perms: object) -> dict[str, str]:
    """רק אזורים ורמות מוכרים (מה שלא מופיע — בלי גישה). טהור."""
    if not isinstance(perms, dict):
        return {}
    return {a: lvl for a, lvl in perms.items() if a in AREAS and lvl in LEVELS}


def load(raw: str | None) -> dict[str, str] | None:
    """מהעמודה admins.perms. None = לא נקבעו (חשבון ישן) — הכול, כמו שהיה."""
    if raw is None:
        return None
    try:
        return clean(json.loads(raw))
    except (ValueError, TypeError):
        return {}


def effective(role: str, perms: dict[str, str] | None) -> dict[str, str]:
    if role == "owner" or perms is None:
        return {a: "edit" for a in AREAS}
    return perms


def can(perms: dict[str, str], area: str, write: bool = False) -> bool:
    lvl = perms.get(area)
    return lvl == "edit" or (lvl == "view" and not write)


def allowed(role: str, perms: dict[str, str] | None, method: str, path: str) -> bool:
    """האם איש הצוות רשאי לבקשה הזו. טהור — נבדק ביחידה."""
    if role == "owner":
        return True
    eff = effective(role, perms)
    parts = path.rstrip("/").split("/")          # ['', 'v1', 'admin', seg, ...]
    seg = parts[3] if len(parts) > 3 and parts[1:3] == ["v1", "admin"] else ""
    write = method.upper() not in ("GET", "HEAD")
    if seg in _ALWAYS:
        return True        # ניהול הצוות עצמו (הוספה / הסרה) — owner_dep
    tail = parts[-1] if len(parts) > 4 else ""
    # מקומות שמשותפים לכמה אזורים
    if seg == "devices" and tail == "avatar" and not write:
        return can(eff, "devices") or can(eff, "support")          # תמונת הלקוח בצ'אט התמיכה
    if seg == "devices" and tail == "crm":
        return False                                                 # רק בעלים
    if seg == "profiles" and not write:
        return can(eff, "profiles") or can(eff, "devices")         # "החלת פרופיל" מדף המכשיר
    if seg == "devices" and tail == "apply-profile":
        return can(eff, "devices", True) or can(eff, "profiles", True)
    if seg in ("devices", "customers") and len(parts) == 4 and not write:
        return can(eff, _AREA_OF[seg]) or can(eff, "profiles")     # רשימות — לבחירה בעמוד הפרופילים
    area = _AREA_OF.get(seg)
    return area is not None and can(eff, area, write)
