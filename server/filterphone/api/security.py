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
