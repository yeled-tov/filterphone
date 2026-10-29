"""קודי הסרה למחשבי Windows (FilterPhone למחשב).

הסרת התוכנה מהמחשב דורשת קוד שהמנהל מפיק בדשבורד (כמו "השהיה לפני הסרה" בטלפון). המחשב בודק את
הקוד בעצמו, גם כשאין אינטרנט — כי בלי ההגנה הגלישה חסומה:

  * לכל מחשב מפתח אקראי של 32 בתים. נוצר בשרת בפעם הראשונה שהמחשב מבקש אותו, נשמר בעמודה
    devices.removal_key, ונשלח למחשב בתוך המנהרה המוצפנת (המחשב שומר אותו מוצפן, למערכת בלבד).
  * הקוד ליום D (יום UTC = שניות מאז 1970 חלקי 86400):
        mac  = HMAC-SHA256(key, "filterphone-removal:{device_id}:{D}")
        bits = 50 הביטים העליונים של mac
        code = 10 תווי Crockford base32 (0-9 A-Z בלי I L O U), בפורמט XXXXX-XXXXX
  * המחשב מקבל קוד של אתמול, היום או מחר (סטייה בשעון) — קוד תקף 24 עד 48 שעות.

המימוש התואם במחשב: filter-phone-desktop, src/FilterPhone.Core/Removal/RemovalCode.cs. וקטור בדיקה
משותף: tests/test_removal.py ו-RemovalCodeTests.cs.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
DAY_S = 86400
_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})


def new_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def day_of(t: int) -> int:
    return t // DAY_S


def code_for(key_b64: str, device_id: int, day: int) -> str:
    key = base64.b64decode(key_b64)
    mac = hmac.new(key, f"filterphone-removal:{device_id}:{day}".encode(), hashlib.sha256).digest()
    n = int.from_bytes(mac[:7], "big") >> 6
    chars = []
    for _ in range(10):
        chars.append(ALPHABET[n & 31])
        n >>= 5
    s = "".join(reversed(chars))
    return f"{s[:5]}-{s[5:]}"


def normalize(code: str) -> str:
    s = "".join(ch for ch in code.upper() if ch.isalnum()).translate(_LOOKALIKES)
    return f"{s[:5]}-{s[5:]}" if len(s) == 10 else ""


def verify(key_b64: str, device_id: int, code: str, t: int) -> bool:
    given = normalize(code)
    if not given or not key_b64:
        return False
    today = day_of(t)
    return any(hmac.compare_digest(given, code_for(key_b64, device_id, d)) for d in (today - 1, today, today + 1))


def valid_until(day: int) -> int:
    """הקוד ליום D מתקבל עד סוף יום D+1."""
    return (day + 2) * DAY_S
