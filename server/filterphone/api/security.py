"""קודי הפעלה, טוקנים, סיסמאות והגבלת קצב."""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# בלי תווים שמתבלבלים (0/O, 1/I/L)
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 10
_WG_KEY = re.compile(r"^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw480]=$")

_ph = PasswordHasher()


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return f"{raw[:5]}-{raw[5:]}"


def normalize_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", code.upper())


def code_hash(code: str) -> str:
    return hashlib.sha256(("fp-code:" + normalize_code(code)).encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(("fp-token:" + token).encode()).hexdigest()


def new_preshared_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def is_wg_key(key: str) -> bool:
    """מפתח ציבורי של WireGuard: 32 בתים ב-base64 (44 תווים, '=' בסוף)."""
    return bool(_WG_KEY.match(key or ""))


_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def clean_text(value: str | None, limit: int, multiline: bool = False) -> str:
    """טקסט חופשי מהמשתמש: בלי תווי בקרה / כיווניות נסתרים, רווחים מצומצמים, באורך מוגבל."""
    text = _CONTROL.sub("", str(value or ""))
    if multiline:
        text = "\n".join(" ".join(line.split()) for line in text.replace("\r", "").split("\n"))
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    else:
        text = " ".join(text.split())
    return text[:limit]


def normalize_phone(phone: str | None) -> str:
    """'050-123 4567' / '+972 50 1234567' → '0501234567' (ישראל) או +<ספרות> (חו"ל). ריק אם לא תקין."""
    raw = str(phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") or raw.startswith("00"):
        digits = digits[2:] if raw.startswith("00") else digits
        if digits.startswith("972"):
            digits = "0" + digits[3:]
        else:
            return "+" + digits if 8 <= len(digits) <= 15 else ""
    return digits if digits.startswith("0") and 9 <= len(digits) <= 10 else ""


def normalize_email(email: str | None) -> str:
    e = str(email or "").strip().lower()
    return e if _EMAIL.match(e) else ""


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return _ph.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


class RateLimiter:
    """חלון זמן נע לכל מפתח (כתובת IP). בזיכרון — מספיק לשרת יחיד."""

    def __init__(self, limit: int, window_s: float):
        self.limit = limit
        self.window = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        t = time.monotonic()
        q = self._hits[key]
        while q and q[0] <= t - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(t)
        return True
