"""מה הגרסה העדכנית של כל אפליקציה ב-Google Play — לטאב "עדכונים" בחנות.

הטלפון שולח את האפליקציות שמותקנות בו (שם חבילה, גרסה, מתי עודכנה), והשרת מחזיר לכל אחת:
    update  — ב-Play יש גרסה חדשה יותר מהמותקנת (כשהגרסה מפורסמת ב-Play),
    maybe   — Play לא מפרסם גרסה ("משתנה לפי מכשיר"), אבל עודכנה שם אחרי שעודכנה בטלפון,
    current — מעודכנת,
    unknown — לא נמצאה ב-Google Play (אפליקציית מערכת / התקנה ישירה), או שלא הספקנו לבדוק.

המידע מ-Play נשמר ב-Redis ל-12 שעות (לא בודקים שוב בכל פתיחה), ובקשה אחת מוגבלת בזמן —
מה שלא הספקנו נבדק בפעם הבאה. הפענוח וההשוואה טהורים (נבדקים בלי רשת).

PlayCatalog — חיפוש אפליקציות ב-Play מהדשבורד ופרטים מלאים בעברית (שם, תיאור, אייקון, צילומי מסך,
מפתח, קטגוריה, דירוג), כדי להוסיף אפליקציה לחנות בלחיצה במקום להקליד הכול.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait

log = logging.getLogger("filterphone.playinfo")

CACHE_TTL_S = 12 * 3600
MISSING_TTL_S = 7 * 86400          # לא ב-Play — לא בודקים שוב שבוע
MAYBE_SLACK_S = 36 * 3600          # Play משחרר עדכונים בהדרגה — "עודכן ב-Play" לא אומר שכבר זמין לכולם
MAX_PACKAGES = 250
_PKG = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
_DS5 = re.compile(r"AF_initDataCallback\(\{key: 'ds:5'.*?data:(.*?), sideChannel: \{\}\}\);", re.S)
_UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126 Mobile Safari/537.36",
       "Accept-Language": "en-US,en;q=0.8"}
_UA_HE = {**_UA, "Accept-Language": "he-IL,he;q=0.9"}
_DETAILS_LINK = re.compile(r"/store/apps/details\?id=([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)")
_TAG = re.compile(r"<[^>]+>")
SEARCH_TTL_S = 3600
SEARCH_LIMIT = 12
MAX_SCREENSHOTS = 8


def _at(d, *path):
    for p in path:
        try:
            d = d[p]
        except (IndexError, TypeError, KeyError):
            return None
    return d


def _ds5(page: str):
    """הנתונים של עמוד האפליקציה (המבנה ש-Play מטמיע בדף), או None."""
    m = _DS5.search(page)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def parse_page(page: str) -> dict:
    """{"version": "6.18" | None, "updated": שניות | None} מתוך עמוד אפליקציה ב-Play."""
    data = _ds5(page)
    if data is None:
        return {"version": None, "updated": None}
    version = _at(data, 1, 2, 140, 0, 0, 0)
    updated = _at(data, 1, 2, 145, 0, 1, 0)
    return {"version": version if isinstance(version, str) and version.strip() else None,
            "updated": int(updated) if isinstance(updated, int | float) and updated > 0 else None}


def _plain(text) -> str:
    """תיאור מ-Play (HTML קצר) → טקסט: <br> → שורה חדשה, בלי תגיות, ישויות מפוענחות."""
    t = re.sub(r"(?i)<br\s*/?>", "\n", str(text or ""))
    t = html.unescape(_TAG.sub("", t))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _https(v) -> str | None:
    return v if isinstance(v, str) and v.startswith("https://") else None


def _str(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def parse_details(page: str) -> dict | None:
    """פרטי האפליקציה מעמוד ב-Play (לחנות): None אם העמוד לא נראה כמו עמוד אפליקציה."""
    data = _ds5(page)
    title = _at(data, 1, 2, 0, 0) if data is not None else None
    if not isinstance(title, str) or not title.strip():
        return None
    shots = [_https(_at(s, 3, 2)) for s in (_at(data, 1, 2, 78, 0) or []) if isinstance(s, list)]
    score = _at(data, 1, 2, 51, 0, 1)
    return {
        "title": title.strip(),
        "summary": _plain(_at(data, 1, 2, 73, 0, 1))[:200],
        "description": _plain(_at(data, 1, 2, 72, 0, 1))[:2000],
        "icon": _https(_at(data, 1, 2, 95, 0, 3, 2)),
        "screenshots": [u for u in shots if u][:MAX_SCREENSHOTS],
        "developer": _str(_at(data, 1, 2, 68, 0)),
        "genre": _str(_at(data, 1, 2, 79, 0, 0, 0)),
        "score": round(float(score), 1) if isinstance(score, int | float) and score > 0 else None,
        "installs": _str(_at(data, 1, 2, 13, 0)),
    }


def parse_search(page: str, limit: int = SEARCH_LIMIT) -> list[str]:
    """שמות החבילות בתוצאות חיפוש ב-Play, לפי הסדר (בלי כפילויות)."""
    return list(dict.fromkeys(_DETAILS_LINK.findall(page)))[:limit]


def version_key(v: str) -> tuple[int, ...]:
    """'2.26.34.81' → (2, 26, 34, 81). אותיות/סיומות (beta, -rc) מתעלמים מהן."""
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:6])


def status(installed: dict, info: dict | None) -> str:
    """installed: {version_name, updated_ms}. info: מה ש-Play אמר (או None = לא נבדק / לא ב-Play)."""
    if not info:
        return "unknown"
    pv, iv = version_key(info.get("version") or ""), version_key(installed.get("version_name") or "")
    if pv and iv:
        return "update" if pv > iv else "current"
    updated, local_ms = info.get("updated"), installed.get("updated_ms")
    if updated and isinstance(local_ms, int | float) and local_ms > 0:
        return "maybe" if updated > local_ms / 1000 + MAYBE_SLACK_S else "current"
    return "unknown"


def _fetch(pkg: str) -> dict | None:
    """None = האפליקציה לא ב-Google Play. שגיאת רשת → זורק (לא שומרים במטמון)."""
    req = urllib.request.Request(f"https://play.google.com/store/apps/details?id={pkg}&hl=en&gl=IL", headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return parse_page(r.read(3_000_000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=_UA_HE)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read(3_000_000).decode("utf-8", "replace")


def fetch_details(pkg: str) -> dict | None:
    """פרטי האפליקציה בעברית. None = לא ב-Google Play. שגיאת רשת → זורק."""
    try:
        return parse_details(_get(f"https://play.google.com/store/apps/details?id={pkg}&hl=iw&gl=IL"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def fetch_search(q: str) -> list[str]:
    return parse_search(_get("https://play.google.com/store/search?c=apps&hl=iw&gl=IL&q="
                             + urllib.parse.quote(q)))


class PlayInfo:
    """בודק ב-Play עם מטמון ב-Redis. בטוח לשימוש מכמה חוטים."""

    def __init__(self, redis, fetch: Callable[[str], dict | None] = _fetch, workers: int = 8,
                 prefix: str = "fp:playinfo:"):
        self.redis = redis
        self.fetch = fetch
        self.workers = workers
        self.prefix = prefix

    def _key(self, pkg: str) -> str:
        return f"{self.prefix}{pkg}"

    def _cached(self, pkgs: list[str]) -> dict[str, dict | None]:
        out: dict[str, dict | None] = {}
        try:
            vals = self.redis.mget([self._key(p) for p in pkgs])
        except Exception as e:                     # Redis לא זמין — פשוט בלי מטמון
            log.debug("playinfo cache read failed: %s", e)
            return out
        for p, v in zip(pkgs, vals, strict=True):
            if v is not None:
                d = json.loads(v)
                out[p] = None if d.get("missing") else d
        return out

    def _store(self, pkg: str, info: dict | None) -> None:
        try:
            self.redis.set(self._key(pkg), json.dumps(info or {"missing": True}),
                           ex=CACHE_TTL_S if info else MISSING_TTL_S)
        except Exception as e:
            log.debug("playinfo cache write failed: %s", e)

    def lookup(self, pkgs: list[str], budget_s: float = 15.0) -> dict[str, dict | None]:
        """מה שבמטמון + מה שהספקנו לבדוק בזמן שהוקצב. חבילה שלא הופיעה בתשובה — לא נבדקה עדיין."""
        pkgs = [p for p in dict.fromkeys(pkgs) if _PKG.match(p)][:MAX_PACKAGES]
        out = self._cached(pkgs)
        todo = [p for p in pkgs if p not in out]
        if not todo:
            return out
        deadline = time.monotonic() + budget_s
        pool = ThreadPoolExecutor(self.workers, thread_name_prefix="playinfo")
        futures = {pool.submit(self.fetch, p): p for p in todo}
        done, _ = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
        pool.shutdown(wait=False, cancel_futures=True)
        for f in done:
            p = futures[f]
            try:
                info = f.result()
            except Exception as e:                 # רשת / Play חסם — ננסה בפעם הבאה
                log.info("play lookup failed for %s: %s", p, e)
                continue
            self._store(p, info)
            out[p] = info
        return out

    def check(self, apps: list[dict], budget_s: float = 15.0) -> dict[str, dict]:
        """apps: [{package, version_name, updated_ms}] → {package: {status, play_version, play_updated}}."""
        info = self.lookup([str(a.get("package", "")) for a in apps], budget_s)
        out = {}
        for a in apps:
            p = str(a.get("package", ""))
            if p not in info and not _PKG.match(p):
                continue
            i = info.get(p)
            out[p] = {"status": status(a, i) if p in info else "pending",
                      "play_version": (i or {}).get("version"), "play_updated": (i or {}).get("updated")}
        return out


class PlayError(Exception):
    """Google Play לא ענה (רשת / חסימה) — שונה מ"לא נמצא"."""


class PlayCatalog:
    """חיפוש ופרטים מלאים מ-Google Play להוספה לחנות מהדשבורד (מטמון ב-Redis כמו PlayInfo)."""

    def __init__(self, redis, details: Callable[[str], dict | None] = fetch_details,
                 search: Callable[[str], list[str]] = fetch_search):
        self.redis = redis
        self.apps = PlayInfo(redis, details, prefix="fp:playapp:")
        self.search_fetch = search

    def app(self, pkg: str) -> dict | None:
        """הפרטים, או None אם האפליקציה לא ב-Play. Play לא ענה — PlayError."""
        if not _PKG.match(pkg):
            return None
        info = self.apps.lookup([pkg], budget_s=12.0)
        if pkg not in info:
            raise PlayError(pkg)
        return info[pkg]

    def search(self, q: str, limit: int = SEARCH_LIMIT) -> list[dict]:
        """תוצאות החיפוש (עד limit), כל אחת עם הפרטים המלאים. Play לא ענה — PlayError."""
        q = " ".join(q.split())[:80]
        key = "fp:playsearch:" + hashlib.sha256(q.lower().encode()).hexdigest()[:32]
        pkgs = None
        try:
            raw = self.redis.get(key)
            pkgs = json.loads(raw) if raw else None
        except Exception as e:
            log.debug("play search cache read failed: %s", e)
        if pkgs is None:
            try:
                pkgs = self.search_fetch(q)
            except Exception as e:
                raise PlayError(q) from e
            try:
                self.redis.set(key, json.dumps(pkgs), ex=SEARCH_TTL_S)
            except Exception as e:
                log.debug("play search cache write failed: %s", e)
        pkgs = [p for p in pkgs if _PKG.match(p)][:limit]
        info = self.apps.lookup(pkgs, budget_s=12.0)
        return [{"package": p, **info[p]} for p in pkgs if info.get(p)]
