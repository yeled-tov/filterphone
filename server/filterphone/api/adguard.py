"""שכבת ה-DNS של החסימות לפי מכשיר: כללים ב-AdGuard Home, עם ‎$client=<כתובת ה-VPN>‎.

הפרוקסי כבר חוסם לפי SNI/Host, אבל לא רואה חיבורים שאינם HTTPS רגיל (למשל השיחות
של וואטסאפ, או אפליקציות שמתחברות לפורטים אחרים). ה-DNS תופס גם אותם.

הכללים נכתבים לבלוק מסומן בתוך "כללים מותאמים אישית" של AdGuard — כל השאר נשאר
כמו שהוא. כתיבה רק כשמשהו השתנה. הגדרות: FP_ADGUARD_URL / _USER / _PASSWORD.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

log = logging.getLogger("filterphone.adguard")

MARK_BEGIN = "! ===== FilterPhone: חסימות לפי מכשיר (מנוהל אוטומטית — לא לערוך כאן) ====="
MARK_END = "! ===== FilterPhone: סוף ====="


def build_rules(devices: Iterable[tuple[str, Iterable[str]]]) -> list[str]:
    """[(כתובת מכשיר, אתרים)] → כללי AdGuard. אתר שחסום לכמה מכשירים — שורה אחת."""
    by_host: dict[str, set[str]] = defaultdict(set)
    for ip, hosts in devices:
        for h in hosts:
            by_host[h.removeprefix("*.")].add(ip)
    return [f"||{h}^$client={'|'.join(sorted(ips))}" for h, ips in sorted(by_host.items())]


def merge(current: list[str], ours: list[str]) -> list[str]:
    """מחליף את הבלוק שלנו בתוך הכללים הקיימים (או מוסיף בסוף)."""
    out, inside = [], False
    for line in current:
        if line == MARK_BEGIN:
            inside = True
            continue
        if line == MARK_END:
            inside = False
            continue
        if not inside:
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    if ours:
        out += [MARK_BEGIN, *ours, MARK_END]
    return out


@dataclass
class AdGuard:
    url: str
    user: str
    password: str
    timeout: float = 5.0
    last_ok: float = 0.0
    last_error: str = ""
    rules: int = 0
    _applied: list[str] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.user and self.password)

    def _request(self, method: str, path: str, body: dict | None = None) -> dict | None:
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        req = urllib.request.Request(
            self.url.rstrip("/") + "/control" + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"})
        # כתובת מקומית — בלי פרוקסי של מערכת, גם אם מוגדר במשתני סביבה
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=self.timeout) as r:
            raw = r.read()
        return json.loads(raw) if raw.strip() else None

    def sync(self, ours: list[str], force: bool = False) -> bool:
        """כותב את הכללים אם השתנו. מחזיר True אם נכתב משהו."""
        if not self.enabled:
            return False
        with self._lock:
            if not force and self._applied == ours:
                return False
            try:
                status = self._request("GET", "/filtering/status") or {}
                current = [str(x) for x in status.get("user_rules") or []]
                merged = merge(current, ours)
                changed = merged != current
                if changed:
                    self._request("POST", "/filtering/set_rules", {"rules": merged})
                self._applied = list(ours)
                self.last_ok, self.last_error, self.rules = time.time(), "", len(ours)
                if changed:
                    log.info("adguard: %d per-device rules applied", len(ours))
                return changed
            except (urllib.error.URLError, OSError, ValueError) as e:
                self.last_error = str(getattr(e, "reason", e))[:200]
                log.warning("adguard sync failed: %s", self.last_error)
                return False

    def state(self) -> dict:
        return {"enabled": self.enabled, "last_ok": int(self.last_ok) or None,
                "error": self.last_error, "rules": self.rules}
