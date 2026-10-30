"""לקוח לשרת הניתוח החיצוני (Modal GPU).

שולח את התמונה הגולמית ל-endpoint ב-Modal, שמריץ את *אותו* קוד המנוע על GPU ומחזיר את
רשומת ה-Entry הסופית (ציונים + גלאי + סגמנטציה לרמה המבוקשת) ואת מפת הסגמנטציה. ההחלטה עצמה
מתקבלת תמיד מקומית מאותם ציונים — אותו קוד מדיניות, אותה החלטה.

עמידות: כל כשל (רשת, timeout, מכסה שנגמרה = 429/5xx, תשובה לא תקינה, גרסת heads שונה) מחזיר
``None``, וה-Engine נופל מיד לניתוח המקומי. אחרי כשל נכנסים ל-cooldown קצר כדי לא לשלם את
ה-timeout על כל תמונה כש-Modal לא זמין/מכסה נגמרה.

מהירות: החיבור ל-Modal נשאר פתוח (keep-alive, חיבור לכל חוט) — בלי לחיצת יד TCP+TLS חדשה לכל
תמונה, שעולה כמה סבבי רשת (מאות מילישניות כש-Modal רחוק).

מכוון: ה-HTTP עצמו ב-[_post] (urllib, חוסם) מופרד מהפענוח/האימות ב-[_parse] (טהור) — כדי
שאפשר לבדוק את שניהם בלי רשת.
"""
from __future__ import annotations

import asyncio
import base64
import http.client
import json
import logging
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse

from ..settings import Settings
from .cache import Entry
from .heads import SCORE_NAMES

log = logging.getLogger("filterphone.engine.remote")

_N_SCORES = len(SCORE_NAMES)


CONFIG_FILE = "modal.json"                  # בתיקיית הנתונים; נכתב מהדשבורד (מערכת → שרת GPU)
MODES = ("always", "overflow", "off")       # תמיד Modal / רק כשהשרת עמוס / כבוי


def config_path(cfg: Settings) -> str:
    return os.path.join(cfg.data_dir, CONFIG_FILE)


def read_config(path: str) -> dict:
    """ההגדרות שנשמרו מהדשבורד. קובץ חסר / פגום = אין הגדרות."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def write_config(path: str, d: dict) -> None:
    """כתיבה אטומית, הרשאות 600 (יש בה סוד)."""
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, path)


def merge_config(current: dict, changes: dict) -> dict:
    """מאמת שינויים מהדשבורד. סוד ריק = בלי שינוי; clear = מחיקת הכול. זורק ValueError בערך לא תקין."""
    if changes.get("clear"):
        return {}
    out = {k: current.get(k, "") for k in ("url", "key", "secret")} | {"mode": current.get("mode", "always")}
    url = changes.get("url")
    if url is not None:
        url = url.strip().rstrip("/")
        if url and not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::\d+)?(?:/[\w./-]*)?", url):
            raise ValueError("כתובת לא תקינה — צריכה להתחיל ב-https:// (למשל https://name--filterphone-engine.modal.run)")
        out["url"] = url
    for k in ("key", "secret"):
        v = changes.get(k)
        if v is None or (k == "secret" and not v.strip()):
            continue
        v = v.strip()
        if len(v) > 200 or re.search(r"\s", v):
            raise ValueError("טוקן לא תקין")
        out[k] = v
    mode = changes.get("mode")
    if mode is not None:
        if mode not in MODES:
            raise ValueError("מצב לא מוכר")
        out["mode"] = mode
    return out


class RemoteEngine:
    """פונה ל-Modal לניתוח, עם נפילה מקומית ו-cooldown. בטוח לשימוש מכמה קורוטינות במקביל."""

    def __init__(self, url: str, key: str, secret: str, heads_version: str,
                 timeout_s: float = 8.0, cooldown_s: float = 30.0):
        self.url = url
        self.key = key
        self.secret = secret
        self.heads_version = heads_version
        self.timeout_s = timeout_s
        self.cooldown_s = cooldown_s
        self.mode = "always"
        self._cooldown_until = 0.0
        self.ewma_s: float | None = None        # זמן תשובה ממוצע (ממוצע נע) של בקשות שהצליחו
        # מתי נשלחה הבקשה האחרונה (למדידה מחדש כש-Modal איטי). "מזמן" = ‎-1e18 ולא 0: השעון (monotonic) מתחיל
        # בהדלקת המחשב, ובשרת שעלה לפני פחות משתי דקות 0 היה נראה "ממש עכשיו"
        self.probe_at = -1e18
        u = urllib.parse.urlsplit(url)
        self._host, self._port = u.hostname or "", u.port or 443
        self._path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        self._ssl = ssl.create_default_context()
        self._local = threading.local()         # חיבור פתוח לכל חוט ב-executor

    @classmethod
    def from_settings(cls, cfg: Settings, heads_version: str,
                      saved: dict | None = None) -> RemoteEngine | None:
        """לקוח רק אם Modal מוגדר (url+key+secret) ולא כבוי; אחרת None — הכול רץ מקומית.
        משתני סביבה (FP_MODAL_*) קודמים; בלעדיהם — מה שנשמר מהדשבורד ([saved])."""
        url, key, secret, mode = cfg.modal_url, cfg.modal_key, cfg.modal_secret, cfg.modal_mode
        if not (url or key or secret) and saved:
            url, key, secret = saved.get("url", ""), saved.get("key", ""), saved.get("secret", "")
            mode = saved.get("mode", mode)
        if mode == "off" or not (url and key and secret):
            return None
        r = cls(url.rstrip("/"), key, secret, heads_version, cfg.modal_timeout_s, cfg.modal_cooldown_s)
        r.mode = mode if mode in MODES else "always"
        return r

    @property
    def enabled(self) -> bool:
        return time.monotonic() >= self._cooldown_until

    def slower_than(self, limit_s: float) -> bool:
        """נמדד ש-Modal עונה לאט מ-limit_s (למשל: רחוק — מעבר לים). בלי מדידה עדיין — False."""
        return self.ewma_s is not None and self.ewma_s > limit_s

    def probe_due(self, every_s: float) -> bool:
        """הגיע הזמן למדוד שוב (בקשה אחת), גם כש-Modal נמדד איטי."""
        return time.monotonic() - self.probe_at >= every_s

    def _trip(self, reason: str) -> None:
        self._cooldown_until = time.monotonic() + self.cooldown_s
        log.warning("Modal unavailable (%s) — using local engine for %.0fs", reason, self.cooldown_s)

    async def analyze(self, data: bytes, level: int) -> tuple[Entry, bytes | None] | None:
        """מחזיר (Entry, PNG-של-סגמנטציה-או-None) מ-Modal, או None בכל כשל (אז נופלים למקומי)."""
        loop = asyncio.get_running_loop()
        t0 = self.probe_at = time.monotonic()
        try:
            payload = await loop.run_in_executor(None, self._post, data, level)
        except Exception as e:
            self._trip(_describe(e))
            return None
        dt = time.monotonic() - t0
        self.ewma_s = dt if self.ewma_s is None else 0.7 * self.ewma_s + 0.3 * dt
        try:
            return self._parse(payload)
        except Exception as e:
            self._trip(f"bad payload: {e}")
            return None

    def _post(self, data: bytes, level: int) -> dict:
        """POST חוסם ל-Modal (רץ ב-executor). זורק על שגיאת HTTP/רשת/timeout.

        על חיבור שכבר פתוח מהפעם הקודמת; חיבור ש-Modal סגר בינתיים (keep-alive שפג) — נפתח
        מחדש ומנסים פעם אחת נוספת (בטוח: ניתוח תמונה לא משנה כלום בצד השני)."""
        headers = {"Content-Type": "application/octet-stream", "Accept": "application/json",
                   "X-FP-Level": str(level), "Modal-Key": self.key, "Modal-Secret": self.secret}
        for attempt in (0, 1):
            conn = getattr(self._local, "conn", None)
            fresh = conn is None
            if fresh:
                conn = http.client.HTTPSConnection(self._host, self._port, timeout=self.timeout_s,
                                                   context=self._ssl)
                self._local.conn = conn
            try:
                conn.request("POST", self._path, body=data, headers=headers)
                resp = conn.getresponse()
                raw = resp.read()
            except (http.client.RemoteDisconnected, http.client.CannotSendRequest, http.client.BadStatusLine,
                    ConnectionResetError, BrokenPipeError):
                self._drop()
                if fresh or attempt:
                    raise
                continue
            except BaseException:
                self._drop()
                raise
            if resp.status >= 400:
                raise urllib.error.HTTPError(self.url, resp.status, resp.reason, resp.headers, None)
            if resp.getheader("connection", "").lower() == "close":
                self._drop()
            return json.loads(raw)
        raise ConnectionError("unreachable")       # לא מגיעים לכאן

    def _drop(self) -> None:
        conn = getattr(self._local, "conn", None)
        self._local.conn = None
        if conn is not None:
            conn.close()

    def _parse(self, payload: dict) -> tuple[Entry, bytes | None]:
        """מאמת את התשובה והופך אותה ל-Entry. זורק אם היא לא תקינה (→ נפילה מקומית)."""
        heads = payload.get("heads")
        if heads != self.heads_version:
            raise ValueError(f"heads mismatch: remote={heads!r} local={self.heads_version!r}")
        raw_entry = payload.get("entry")
        if raw_entry is None:
            raise ValueError("missing entry")
        entry = Entry.load(raw_entry) if isinstance(raw_entry, str) else Entry(**raw_entry)
        _validate(entry)
        labels_b64 = payload.get("labels")
        labels = base64.b64decode(labels_b64) if labels_b64 else None
        return entry, labels


def make_payload(entry: Entry, labels: bytes | None, heads_version: str) -> dict:
    """התשובה שהשרת ב-Modal מחזיר (ו-[RemoteEngine._parse] מפענח). משותף לשני הצדדים."""
    return {
        "entry": entry.dump(),
        "labels": base64.b64encode(labels).decode("ascii") if labels else None,
        "heads": heads_version,
    }


def _validate(entry: Entry) -> None:
    """בדיקת שפיות על רשומה שהגיעה מבחוץ — לפני שסומכים עליה להחלטת סינון."""
    s = entry.scores
    if s is not None:
        if not isinstance(s, list) or len(s) != _N_SCORES:
            raise ValueError(f"scores must be {_N_SCORES} numbers, got {s!r}")
        if not all(isinstance(x, int | float) for x in s):
            raise ValueError("scores must be numeric")
    if entry.dets is not None and not isinstance(entry.dets, list):
        raise ValueError("dets must be a list")
    if entry.seg is not None and not isinstance(entry.seg, dict):
        raise ValueError("seg must be an object")


def _describe(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    if isinstance(e, TimeoutError | urllib.error.URLError):
        return f"network: {e}"
    return f"{type(e).__name__}: {e}"
