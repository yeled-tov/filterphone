"""הלוגיקה העסקית של ה-API: הרשמה ובקשות, מכשירים, חסימות, קודי הפעלה, סנכרון.

נפרד מ-FastAPI כדי שאפשר יהיה לבדוק אותו ישירות ולהשתמש בו מכלי שורת פקודה.

מחזור החיים של מכשיר:
    הרשמה מהאפליקציה → pending (בקשת הצטרפות, בלי VPN)
        → המנהל מאשר (אחרי תשלום): כתובת VPN, רמה וחסימות → active
        → או דוחה → rejected
    הפעלה בקוד (הדרך הקודמת) → active מיד.
    active ↔ disabled — מהממשק.

מקור האמת הוא מסד הנתונים. ממנו נגזרים: WireGuard (מי מחובר), Redis (מדיניות לפרוקסי),
ו-AdGuard (חסימות DNS לפי מכשיר).
"""
from __future__ import annotations

import base64
import functools
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import redis

from .. import protocol
from ..proxy.policy import LEVELS_KEY, POLICY_KEY, POLICY_REV_KEY
from ..settings import Settings
from . import compliance, db, security
from .adguard import AdGuard, build_rules
from .apk import ApkError, read_apk
from .catalog import (
    CATALOG,
    DEFAULT_OPTIONS,
    KNOWN_APP_HOSTS,
    LEVELS,
    Compiled,
    DeviceSettings,
    clean_cert,
    clean_package,
    compile_settings,
    policy_checksum,
    summary,
)
from .fetch import FetchError, fetch_image
from .observe import REGISTRY as API_METRICS
from .wg import WireGuard, WireGuardError

log = logging.getLogger("filterphone.api")

ORG_NAME_DEFAULT = "פילטר פון"     # מוצג בטלפון: "המכשיר הזה שייך ל-פילטר פון"

# ---- החנות
STORE_SEED_VERSION = 3
STORE_MEDIA_TTL_S = 30 * 86400          # תמונות חנות מסוננות נשמרות חודש (כתובת חדשה = קובץ חדש)
STORE_MEDIA_MAX_BYTES = 3_000_000
FT_STORE, FT_FULL = "com.filtertube.filtertube", "com.filtertube.app"
FT_DESC = (
    "FilterTube — יוטיוב עם סינון תוכן מובנה. עובד מחוץ ל-VPN (יוטיוב חוסם כתובות של שרתים), "
    "והסינון מובנה בתוך האפליקציה עצמה — כך שהיא פועלת גם כשהסינון של FilterPhone פעיל.\n\n"
    "שלוש רמות סינון:\n"
    "• רמה 1 — מחמיר: רק ערוצים/תכנים מאושרים.\n"
    "• רמה 2 — בינוני: חסימה רחבה של תוכן לא ראוי, חיפוש מוגבל.\n"
    "• רמה 3 — בסיסי: חסימת תוכן בוטה בלבד.\n\n"
    "את הרמה בוחרים בתוך FilterTube. היא מחליפה את יוטיוב הרגיל (שנחסם לפי ההגדרות שלכם)."
)
FT_NOTE = "יוטיוב כשר עם 3 רמות סינון — התקנה ישירה (העלו את ה-APK בדשבורד)."
# הזריעה של גרסה 1 (כפי שהייתה) — כדי לזהות מה המנהל עוד לא שינה
_FT_V1 = (
    {"package": FT_STORE, "label": "FilterTube — גרסת החנות", "category": "וידאו",
     "note": "יוטיוב כשר עם 3 רמות סינון — מהחנות הרשמית.",
     "play_url": f"https://play.google.com/store/apps/details?id={FT_STORE}", "sort": 1},
    {"package": FT_FULL, "label": "FilterTube", "category": "וידאו",
     "note": "יוטיוב כשר עם 3 רמות סינון — הגרסה המלאה.", "sort": 2},
)


@functools.lru_cache(maxsize=1)
def _play_titles() -> dict[str, list[str]]:
    seed = store_seed()
    # FilterTube מותקנת ישירות (לא מ-Google Play) — לכן לא נכללת ברשימת הכותרות של חלון ה-Play.
    return {a["package"]: a.get("play_titles", []) for a in seed["apps"]}


@functools.lru_cache(maxsize=1)
def store_seed() -> dict:
    """אפליקציות בסיסיות מאומתות מול Google Play (שם חבילה, אייקון, תיאור, צילומי מסך) + סדר הקטגוריות."""
    with open(Path(__file__).with_name("store_seed.json"), encoding="utf-8") as f:
        return json.load(f)
LEVEL_NAMES = {k: v["name"] for k, v in LEVELS.items()}
ONLINE_WINDOW_S = 180           # WireGuard מחדש handshake כל 2 דקות בערך
EVENTS_KEY = "fp:events"
PASSTHROUGH_KEY = "fp:pt"
SYNC_MARK_KEY = "fp:policy:synced"     # נכתב בכל סנכרון מלא; אם נעלם — Redis אותחל, מסנכרנים שוב
STAT_FIELDS = ("scanned", "block", "replace", "site", "video")
MAX_REPORTED_APPS = 800
LAST_SEEN_EVERY_S = 60                  # כתיבת last_seen_at למסד לכל היותר פעם בדקה למכשיר


def observe_snapshot() -> dict[str, float]:
    return API_METRICS.snapshot()


def mask_phone(phone: str) -> str:
    """ללוגים: '0501234567' → '…567'. מספר מלא נשמר רק במסד."""
    return f"…{phone[-3:]}" if phone else ""


# מה שהטלפון מדווח ב-heartbeat. רק הסוגים והשדות האלה נשמרים (בלי פרטים אישיים).
_HB_BOOL = ("device_owner", "device_admin", "battery_unrestricted", "background_restricted", "ca_installed",
            "notifications")
_HB_SECTIONS = {
    "vpn": {"state": str, "handshake_age_s": int, "always_on": bool, "lockdown": bool, "last_health_ok": int,
            "reconnects": int, "error": str,
            # בדיקת תעבורה אמיתית במנהרה (DataProbe): handshake טרי לא מוכיח שיש אינטרנט
            "data_ok": bool, "data_error": str, "data_fail_age_s": int, "data_fails": int},
    "accessibility": {"enabled": bool, "running": bool, "last_event_age_s": int, "required": bool,
                      "self_heal": bool, "restored": int, "errors": int, "error": str},
    "tamper": {"adb": bool, "dev_options": bool, "safe_mode": bool, "unknown_sources": bool},
    "dpc": {"applied": list, "failed": list, "always_on_vpn": str, "lockdown": bool, "frp": bool},
    "protections": {"wa_profile_photo": int, "screens_blocked": int, "apps_blocked": int,
                    "keywords_blocked": int},
    "capabilities": {"sdk": int, "manufacturer": str, "model": str, "device_owner": bool,
                     "profile_owner": bool, "provisioning_allowed": bool, "root": bool,
                     "accessibility": bool, "always_on": bool, "lockdown": bool},
    "update": {"state": str, "version": str, "error": str},
}
# מעברים שנרשמים ביומן המכשיר
_LOG_TRANSITIONS = (
    (("vpn", "state"), "UP", "vpn_up", "vpn_down"),
    (("accessibility", "enabled"), True, "accessibility_on", "accessibility_off"),
    (("device_owner",), True, "management_on", "management_removed"),
    (("battery_unrestricted",), True, "battery_unrestricted", "battery_restricted"),
)


def _clip(v, typ):
    if typ is bool:
        return v if isinstance(v, bool) else None
    if typ is int:
        return int(v) if isinstance(v, int | float) and not isinstance(v, bool) and abs(v) < 10**12 else None
    if typ is str:
        return security.clean_text(str(v), 80) if isinstance(v, str) else None
    if typ is list:
        return [security.clean_text(str(x), 40) for x in v[:30]] if isinstance(v, list) else None
    return None


def sanitize_heartbeat(hb: dict) -> dict:
    out: dict = {}
    for k in _HB_BOOL:
        if isinstance(hb.get(k), bool):
            out[k] = hb[k]
    for k in ("app_version", "private_dns", "policy_error", "preflight", "sync_mode"):
        if isinstance(hb.get(k), str) and hb[k]:
            out[k] = security.clean_text(hb[k], 80)
    for k in ("sdk", "policy_version", "last_policy_sync", "ts"):
        v = _clip(hb.get(k), int)
        if v is not None:
            out[k] = v
    for section, fields in _HB_SECTIONS.items():
        raw = hb.get(section)
        if isinstance(raw, dict):
            sec = {f: v for f, t in fields.items() if (v := _clip(raw.get(f), t)) is not None}
            if sec:
                out[section] = sec
    if (bypass := _clean_bypass_report(hb.get("vpn_bypass"))) is not None:
        out["vpn_bypass"] = bypass
    return out


def _clean_bypass_report(raw) -> dict | None:
    """דיווח 'מחוץ ל-VPN': applied (מוחרגות עכשיו), mismatch (חתימה לא תואמת לנעילה),
    certs (חבילה → SHA-256 של תעודת החתימה — מידע ציבורי שבכל APK), lockdown_conflict (אנדרואיד 9-)."""
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    for k in ("applied", "mismatch"):
        if isinstance(raw.get(k), list):
            out[k] = sorted({p for p in (clean_package(str(x)) for x in raw[k][:20]) if p})
    if isinstance(raw.get("certs"), dict):
        certs = {}
        for pkg, cert in list(raw["certs"].items())[:20]:
            p, c = clean_package(str(pkg)), clean_cert(cert)
            if p and c:
                certs[p] = c
        out["certs"] = certs
    if isinstance(raw.get("lockdown_conflict"), bool):
        out["lockdown_conflict"] = raw["lockdown_conflict"]
    return out or None


def _dig(d: dict, path: tuple[str, ...]):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d

# הגרסה שפורסמה בדף ההורדה — נכתב בבנייה (tools/apk_release.py) ומתפרסם יחד עם קובצי ה-APK
RELEASE_MANIFEST = "FilterPhone.json"
# מדיניות פרוקסי "שקופה" למכשיר בהשהיה (level 0 — בלי סינון תמונות)
_PASSTHROUGH_POLICY = json.dumps({"level": 0, "hosts": [], "paths": [], "images": "filter",
                                  "video": False, "app_images": "allow"}, separators=(",", ":"))
MAX_PAUSE_MINUTES = 1440

# פרופילי ברירת מחדל: (שם, רמה, אפשרויות, שדות נוספים של DeviceSettings). בתוך מודל החסימה הקיים —
# בלי "הבטחות" של allowlist/קיוסק שהמנוע לא תומך בהן. המנהל יכול לערוך, להוסיף ולהחיל על מכשיר.
SEED_PROFILES: list[tuple[str, int, list[str], dict]] = [
    ("מחמיר — סינון תמונות מלא", 1,
     ["whatsapp_profile", "whatsapp_status", "play_store", "google_images", "browsers_other"],
     {"keywords": {"enabled": True}}),
    ("בינוני — לבוש צנוע עובר", 2, ["whatsapp_profile", "google_images"], {}),
    ("בלי חנות ובלי דפדפנים", 2, ["play_store", "browsers_all"], {}),
    ("וואטסאפ מוגן בלבד (בלי מסנג'רים אחרים)", 2,
     ["whatsapp_profile", "whatsapp_status", "telegram", "viber", "discord"], {}),
    ("בלי רשתות חברתיות", 2,
     ["facebook", "instagram", "tiktok", "twitter", "threads", "snapchat", "pinterest", "reddit"], {}),
    ("בסיסי — רק עירום מפורש", 3, ["play_store"], {}),
]
_RELEASE_NAME = re.compile(r"^FilterPhone[A-Za-z0-9._-]{0,40}\.apk$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_UID_RE = re.compile(r"^[0-9a-f]{16,64}$")
_ABI = re.compile(r"^[a-z0-9_-]{1,20}$")
MAX_APK_BYTES = 200 * 1024 * 1024

STATUS_MESSAGES = {
    "pending": "הבקשה התקבלה ומחכה לאישור. נעדכן ברגע שהחיבור ייפתח.",
    "rejected": "הבקשה לא אושרה.",
    "disabled": "המכשיר הושבת על ידי המנהל.",
}


# keepalive של WireGuard: מתחת ל-15 — סוללה בלי תועלת; מעל 50 — ה-NAT של הסלולר עלול לסגור את המיפוי
# (התראות push מתעכבות), וגיל ה-handshake עובר את ספי "מחובר" (180 שניות) בטלפון ובשרת.
KEEPALIVE_RANGE = (15, 50)


def keepalive_s(value: int) -> int:
    lo, hi = KEEPALIVE_RANGE
    return max(lo, min(hi, int(value)))


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _json_list(raw: str | None) -> list:
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except ValueError:
        return []


def _json_dict(raw: str | None) -> dict:
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


@dataclass
class Services:
    cfg: Settings
    wg: WireGuard
    redis: redis.Redis
    adguard: AdGuard | None = None
    dirty: threading.Event = field(default_factory=threading.Event)   # משהו השתנה — לסנכרן DNS
    redis_stale: bool = False           # כתיבה ל-Redis נכשלה — הסנכרון התקופתי יכתוב הכול מחדש

    def __post_init__(self) -> None:
        if self.adguard is None:
            self.adguard = AdGuard(self.cfg.adguard_url, self.cfg.adguard_user, self.cfg.adguard_password)
        self.pool = db.Pool(self.cfg.db_path, size=self.cfg.db_pool_size)
        try:
            self.backfill_policy_checksums()
            self.refresh_stale_policies()
            self.seed_profiles()
            self.seed_store()
        except sqlite3.Error as e:            # מסד ישן שעוד לא עבר הגירה (כלי שורת פקודה) — לא עוצרים
            log.warning("startup data step skipped: %s", e)

    def db(self):
        """חיבור מהמאגר: with self.db() as c: ..."""
        return self.pool.connection()

    def conn(self) -> sqlite3.Connection:
        """חיבור נפרד (לכלי שורת הפקודה) — לסגור בעצמכם."""
        return db.connect(self.cfg.db_path)

    # ------------------------------------------------------------ תעודה

    def ca_pem(self) -> str:
        return Path(self.cfg.mitm_ca_cert).read_text(encoding="ascii")

    def ca_fingerprint(self) -> str:
        pem = self.ca_pem()
        body = "".join(line for line in pem.splitlines() if line and not line.startswith("-----"))
        return hashlib.sha256(base64.b64decode(body)).hexdigest()

    def ca_info(self) -> dict:
        return {"pem": self.ca_pem(), "sha256": self.ca_fingerprint(), "name": "FilterPhone CA"}

    # ------------------------------------------------------------ יומן פעולות

    def audit(self, actor: str, action: str, target: str = "", detail: str = "",
              c: sqlite3.Connection | None = None) -> None:
        sql = "INSERT INTO audit (ts, actor, action, target, detail) VALUES (?,?,?,?,?)"
        args = (db.now(), actor[:60], action[:60], target[:120], detail[:500])
        if c is not None:
            c.execute(sql, args)
            return
        with self.db() as c2:
            c2.execute(sql, args)

    def list_audit(self, limit: int = 100) -> list[dict]:
        with self.db() as c:
            rows = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))
            return [dict(r) for r in rows]

    # ------------------------------------------------------------ גרסאות מדיניות

    @staticmethod
    def _next_revision(c: sqlite3.Connection) -> int:
        c.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'policy_revision'")
        return int(c.execute("SELECT value FROM meta WHERE key = 'policy_revision'").fetchone()[0])

    def bump_policy(self, c: sqlite3.Connection, device_id: int) -> int:
        """בתוך טרנזקציה, אחרי שינוי: אם מה שהטלפון צריך לאכוף השתנה — גרסה חדשה. מחזיר את הגרסה."""
        row = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        checksum = policy_checksum(row["level"], self.compiled(row))
        if checksum == row["policy_checksum"]:
            return row["policy_version"]
        version = row["policy_version"] + (1 if row["policy_checksum"] else 0)
        c.execute("UPDATE devices SET policy_version = ?, policy_checksum = ?, policy_updated_at = ?,"
                  " policy_revision = ? WHERE id = ?",
                  (version, checksum, db.now(), self._next_revision(c), device_id))
        return version

    def backfill_policy_checksums(self) -> int:
        """מכשירים מלפני ההגירה (בלי טביעה): מחשבים טביעה בלי להעלות גרסה."""
        with self.db() as c:
            rows = c.execute("SELECT * FROM devices WHERE policy_checksum = ''").fetchall()
            for r in rows:
                c.execute("UPDATE devices SET policy_checksum = ?, policy_updated_at = COALESCE(policy_updated_at, ?)"
                          " WHERE id = ? AND policy_checksum = ''",
                          (policy_checksum(r["level"], self.compiled(r)), db.now(), r["id"]))
        return len(rows)

    def refresh_stale_policies(self) -> int:
        """אחרי עדכון שרת שמשנה את מה שהטלפון אוכף (קטלוג, ברירות מחדל): גרסת מדיניות חדשה רק
        למכשירים שהטביעה שלהם השתנתה — כדי שיקבלו את השינוי בדיווח הבא. דטרמיניסטי: בלי שינוי אין גרסה."""
        bumped = 0
        with self.db() as c, db.transaction(c):
            rows = c.execute("SELECT id, level, policy, policy_checksum FROM devices WHERE policy_checksum != ''")
            for r in rows.fetchall():
                if policy_checksum(r["level"], self.compiled(r)) != r["policy_checksum"]:
                    self.bump_policy(c, r["id"])
                    bumped += 1
        if bumped:
            log.info("policy refreshed for %d devices after a catalog change", bumped)
        return bumped

    # ------------------------------------------------------------ שם הארגון במכשירים

    def org_name(self) -> str:
        """השם שאנדרואיד מציג במסך הנעילה ובפס ההתראות: "המכשיר הזה שייך ל-…" (מנהל מכשיר בלבד)."""
        with self.db() as c:
            r = c.execute("SELECT value FROM meta WHERE key = 'org_name'").fetchone()
        return r["value"] if r and r["value"] else ORG_NAME_DEFAULT

    def set_org_name(self, admin: str, name: str) -> str:
        name = " ".join((name or "").split())
        if not 1 <= len(name) <= 60 or any(ord(ch) < 32 for ch in name):
            raise ApiError(400, "שם הארגון: 1–60 תווים")
        with self.db() as c, db.transaction(c):
            c.execute("INSERT INTO meta (key, value) VALUES ('org_name', ?)"
                      " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (name,))
        self.audit(admin, "org_name", "branding", name)
        return name

    def device_policy(self, row: sqlite3.Row) -> dict:
        """המדיניות לטלפון, עם גרסה וטביעה (הטלפון מאשר את הגרסה שהחיל)."""
        return {**self.compiled(row).device_json(), "version": row["policy_version"],
                "updated_at": row["policy_updated_at"], "checksum": row["policy_checksum"],
                "revision": row["policy_revision"]}

    # ------------------------------------------------------------ יומן מכשיר

    @staticmethod
    def log_device(c: sqlite3.Connection, device_id: int, kind: str, detail: str = "") -> None:
        c.execute("INSERT INTO device_log (device_id, ts, kind, detail) VALUES (?,?,?,?)",
                  (device_id, db.now(), kind[:40], detail[:200]))

    def device_log(self, device_id: int, limit: int = 50) -> list[dict]:
        with self.db() as c:
            rows = c.execute("SELECT ts, kind, detail FROM device_log WHERE device_id = ? ORDER BY id DESC LIMIT ?",
                             (device_id, max(1, min(limit, 500)))).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ מדיניות → Redis / AdGuard

    @staticmethod
    def compiled(row: sqlite3.Row) -> Compiled:
        return compile_settings(row["level"], DeviceSettings.load(row["policy"]))

    @staticmethod
    def is_paused(row: sqlite3.Row, t: int | None = None) -> bool:
        """ההגנה מושהית (מצב תחזוקה שהמנהל אישר, עם תוקף)."""
        pu = row["pause_until"] if "pause_until" in row.keys() else None
        return bool(pu and pu > (t if t is not None else db.now()))

    def push_device(self, row: sqlite3.Row) -> None:
        """מעדכן את הפרוקסי (Redis) לגבי מכשיר אחד, ומסמן שצריך לסנכרן את ה-DNS.
        בהשהיה (pause) — סינון התמונות בפרוקסי כבוי (level 0, passthrough) עד שהתוקף פג."""
        ip = row["vpn_ip"]
        if not ip:
            return
        try:
            pipe = self.redis.pipeline()
            if row["status"] == "active" and self.is_paused(row):
                pipe.hset(LEVELS_KEY, ip, 0)
                pipe.hset(POLICY_KEY, ip, _PASSTHROUGH_POLICY)
            elif row["status"] == "active":
                pipe.hset(LEVELS_KEY, ip, row["level"])
                pipe.hset(POLICY_KEY, ip, self.compiled(row).proxy_json(row["level"]))
            else:
                pipe.hdel(LEVELS_KEY, ip)
                pipe.hdel(POLICY_KEY, ip)
            pipe.incr(POLICY_REV_KEY)           # הפרוקסי טוען מחדש תוך 3 שניות
            pipe.execute()
        except redis.RedisError as e:
            # הסנכרון התקופתי (sync_redis_if_needed) יתקן — מסמנים שצריך
            log.error("redis policy sync failed for %s: %s", ip, e)
            self.redis_stale = True
        self.dirty.set()

    def _active_devices(self) -> list[sqlite3.Row]:
        with self.db() as c:
            return c.execute("SELECT * FROM devices WHERE status = 'active' AND vpn_ip IS NOT NULL").fetchall()

    def sync_wireguard(self) -> tuple[int, int]:
        """מיישר את ממשק WireGuard לפי מסד הנתונים. מחזיר (נוספו, הוסרו)."""
        active = self._active_devices()
        return self.wg.reconcile({r["public_key"]: (r["preshared_key"], r["vpn_ip"]) for r in active})

    def sync_redis(self) -> None:
        """כותב מחדש את טבלאות המדיניות שהפרוקסי קורא (בטרנזקציה אחת — הפרוקסי לא רואה חצי טבלה)."""
        active = self._active_devices()
        pipe = self.redis.pipeline(transaction=True)
        pipe.delete(LEVELS_KEY, POLICY_KEY)
        if active:
            pipe.hset(LEVELS_KEY, mapping={r["vpn_ip"]: r["level"] for r in active})
            pipe.hset(POLICY_KEY, mapping={r["vpn_ip"]: self.compiled(r).proxy_json(r["level"]) for r in active})
        pipe.incr(POLICY_REV_KEY)
        pipe.set(SYNC_MARK_KEY, db.now())
        pipe.execute()
        self.redis_stale = False

    def sync_redis_if_needed(self) -> bool:
        """אם Redis אותחל / נמחק (הסימון חסר), או שכתיבה קודמת נכשלה — סנכרון מלא. מחזיר True אם סונכרן."""
        if not self.redis_stale and self.redis.exists(SYNC_MARK_KEY):
            return False
        log.warning("redis policy tables missing or stale — resyncing from the database")
        self.sync_redis()
        return True

    def adguard_rules(self) -> list[str]:
        return build_rules((r["vpn_ip"], self.compiled(r).hosts) for r in self._active_devices())

    def sync_adguard(self, force: bool = False) -> bool:
        assert self.adguard is not None
        return self.adguard.sync(self.adguard_rules(), force=force)

    def sync_all(self) -> None:
        """מיישר WireGuard, Redis ו-AdGuard לפי מסד הנתונים. כל אחד בנפרד — כשל באחד לא עוצר אחר."""
        self.backfill_policy_checksums()
        errors = []
        for step in (self.sync_wireguard, self.sync_redis, self.sync_adguard):
            try:
                step()
            except (WireGuardError, redis.RedisError) as e:
                errors.append(f"{step.__name__}: {e}")
        if errors:
            raise RuntimeError("; ".join(errors))

    # ------------------------------------------------------------ כתובות VPN

    def _allocate_ip(self, c: sqlite3.Connection) -> str:
        net = ipaddress.ip_network(self.cfg.vpn_network)
        used = {r["vpn_ip"] for r in c.execute("SELECT vpn_ip FROM devices WHERE vpn_ip IS NOT NULL")}
        used.add(self.cfg.vpn_server_ip)
        for host in net.hosts():
            if int(host) - int(net.network_address) < self.cfg.vpn_first_client:
                continue
            if str(host) not in used:
                return str(host)
        raise ApiError(503, "אין כתובות פנויות ב-VPN")

    def vpn_config(self, ip: str, psk: str) -> dict:
        out = {
            "address": f"{ip}/32",
            "dns": [self.cfg.vpn_server_ip],
            "mtu": 1280,
            "server_public_key": self.wg.public_key(),
            "preshared_key": psk,
            "endpoint": self.cfg.vpn_endpoint,
            "allowed_ips": ["0.0.0.0/0", "::/0"],
            "persistent_keepalive": keepalive_s(self.cfg.wg_keepalive_s),
        }
        if self.cfg.public_ip:
            # מיד אחרי הפעלת הטלפון לפעמים אין עדיין DNS — אז מתחברים לפי הכתובת
            port = self.cfg.vpn_endpoint.rpartition(":")[2] or "51820"
            out["endpoint_fallback"] = f"{self.cfg.public_ip}:{port}"
        return out

    def rekey(self, row: sqlite3.Row, public_key: str) -> dict:
        """הטלפון איבד את המפתח הפרטי (למשל ה-Keystore נמחק אחרי עדכון מערכת) ויצר חדש.
        מאומת בטוקן המכשיר; אותה כתובת VPN, אותה מדיניות — רק המפתח מתחלף."""
        if row["status"] != "active" or not row["vpn_ip"]:
            raise ApiError(403, STATUS_MESSAGES.get(row["status"], "המכשיר לא פעיל"))
        if not security.is_wg_key(public_key):
            raise ApiError(400, "מפתח ציבורי לא תקין")
        if public_key == row["public_key"]:
            return self.device_vpn(row)
        with self.db() as c, db.transaction(c):
            if c.execute("SELECT 1 FROM devices WHERE public_key = ? AND id != ?", (public_key, row["id"])).fetchone():
                raise ApiError(409, "המפתח כבר רשום למכשיר אחר")
            c.execute("UPDATE devices SET public_key = ? WHERE id = ?", (public_key, row["id"]))
            self.audit(f"device:{row['id']}", "rekey", f"device:{row['id']}", "", c)
            self.log_device(c, row["id"], "rekey", "מפתח VPN חדש")
            # אם WireGuard נכשל — הטרנזקציה מתבטלת והמפתח הישן נשאר
            self.wg.add_peer(public_key, row["preshared_key"], row["vpn_ip"])
            try:
                self.wg.remove_peer(row["public_key"])
            except WireGuardError as e:
                log.warning("old peer removal failed (reconcile will fix): %s", e)
            fresh = c.execute("SELECT * FROM devices WHERE id = ?", (row["id"],)).fetchone()
        log.info("device %s rotated its VPN key", row["id"])
        return self.device_vpn(fresh)

    # ------------------------------------------------------------ הפעלה בקוד

    def enroll(self, code: str, public_key: str, name: str, platform: str, app_version: str,
               device_uid: str = "") -> dict:
        if not security.is_wg_key(public_key):
            raise ApiError(400, "מפתח ציבורי לא תקין")
        name = (name or "").strip()[:60] or "מכשיר"
        token = security.new_token()
        psk = security.new_preshared_key()
        with self.db() as c, db.transaction(c):
            row = c.execute("SELECT * FROM codes WHERE code_hash = ?", (security.code_hash(code),)).fetchone()
            if row is None or row["uses_left"] <= 0 or (row["expires_at"] and row["expires_at"] < db.now()):
                raise ApiError(403, "קוד ההפעלה לא תקין או שפג תוקפו")
            if c.execute("SELECT 1 FROM devices WHERE public_key = ?", (public_key,)).fetchone():
                raise ApiError(409, "המכשיר כבר רשום")
            ip = self._allocate_ip(c)
            t = db.now()
            # הקוד נושא את אפשרויות ברירת המחדל (רשתות, דפדפן, חנויות...) — מחילים אותן על המכשיר
            code_opts = sorted({o for o in _json_list(row["options"]) if o in CATALOG})
            policy_json = DeviceSettings(options=code_opts).dump()
            cur = c.execute(
                "INSERT INTO devices (name, label, platform, app_version, public_key, preshared_key, vpn_ip,"
                " level, token_hash, code_id, device_uid, policy, created_at, approved_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, row["label"], platform[:20], app_version[:20], public_key, psk, ip, row["level"],
                 security.token_hash(token), row["id"], self.clean_uid(device_uid), policy_json, t, t))
            c.execute("UPDATE codes SET uses_left = uses_left - 1 WHERE id = ?", (row["id"],))
            device_id, level = cur.lastrowid, row["level"]
            self.bump_policy(c, device_id)
            self.audit(f"code:…{row['hint']}", "enroll", f"device:{device_id}", name, c)
            # אם WireGuard נכשל — הטרנזקציה מתבטלת, הקוד לא נשרף
            self.wg.add_peer(public_key, psk, ip)
            dev = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        self.push_device(dev)
        log.info("enrolled device %s (%s) at %s, level %s", device_id, name, ip, level)
        return {
            "device_id": device_id,
            "token": token,
            "status": "active",
            "level": level,
            "level_name": LEVEL_NAMES.get(level, str(level)),
            "vpn": self.vpn_config(ip, psk),
            "ca": self.ca_info(),
        }

    # ------------------------------------------------------------ החלפת מכשיר (ישן → חדש)

    TRANSFER_TTL_S = 24 * 3600

    def create_transfer(self, device_id: int, admin: str) -> dict:
        """המנהל מתחיל החלפת מכשיר: טוקן חד-פעמי (24ש') שאיתו הטלפון החדש יירשם ויירש את הלקוח,
        הרמה והמדיניות של הישן. הטלפון הישן מושבת ברגע שההחלפה מתבצעת. נרשם ב-audit."""
        token = "FPT-" + security.new_token()     # קידומת לזיהוי בטלפון (קוד החלפה, לא קוד הפעלה רגיל)
        with self.db() as c, db.transaction(c):
            row = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if row is None:
                raise ApiError(404, "מכשיר לא נמצא")
            exp = db.now() + self.TRANSFER_TTL_S
            c.execute("UPDATE devices SET transfer_token = ?, transfer_expires = ? WHERE id = ?",
                      (security.token_hash(token), exp, device_id))
            self.log_device(c, device_id, "transfer_started")
            self.audit(admin, "transfer_start", f"device:{device_id}", "", c)
        return {"token": token, "expires_at": db.now() + self.TRANSFER_TTL_S}

    def transfer_enroll(self, token: str, public_key: str, name: str, platform: str, app_version: str,
                        device_uid: str = "") -> dict:
        """הטלפון החדש נרשם עם טוקן ההחלפה: מקבל כתובת ומפתחות חדשים, יורש לקוח/רמה/מדיניות מהישן,
        והישן מושבת (anti-replay: הטוקן חד-פעמי ונמחק, ובדיקת תוקף)."""
        if not security.is_wg_key(public_key):
            raise ApiError(400, "מפתח ציבורי לא תקין")
        name = (name or "").strip()[:60] or "מכשיר"
        new_token = security.new_token()
        psk = security.new_preshared_key()
        with self.db() as c, db.transaction(c):
            old = c.execute("SELECT * FROM devices WHERE transfer_token = ? AND transfer_token != ''",
                            (security.token_hash(token),)).fetchone()
            if old is None or not old["transfer_expires"] or old["transfer_expires"] < db.now():
                raise ApiError(403, "קוד ההחלפה לא תקין או שפג תוקפו")
            if c.execute("SELECT 1 FROM devices WHERE public_key = ?", (public_key,)).fetchone():
                raise ApiError(409, "המכשיר כבר רשום")
            ip = self._allocate_ip(c)
            t = db.now()
            cur = c.execute(
                "INSERT INTO devices (name, label, platform, app_version, public_key, preshared_key, vpn_ip,"
                " level, policy, customer_id, token_hash, code_id, device_uid, created_at, approved_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, old["label"], platform[:20], app_version[:20], public_key, psk, ip, old["level"],
                 old["policy"], old["customer_id"], security.token_hash(new_token), old["code_id"],
                 self.clean_uid(device_uid), t, t))
            new_id = cur.lastrowid
            # הישן: מושבת, הטוקן נשרף (חד-פעמי), והחיבור שלו מוסר
            c.execute("UPDATE devices SET status = 'disabled', disabled_reason = 'transferred', transfer_token = '',"
                      " transfer_expires = NULL WHERE id = ?", (old["id"],))
            self.bump_policy(c, new_id)
            self.log_device(c, old["id"], "transfer_done", f"→ מכשיר {new_id}")
            self.log_device(c, new_id, "transfer_in", f"← מכשיר {old['id']}")
            self.audit("transfer", "transfer_done", f"device:{new_id}", f"from device:{old['id']}", c)
            if old["public_key"]:
                self.wg.remove_peer(old["public_key"])
            self.wg.add_peer(public_key, psk, ip)
            new = c.execute("SELECT * FROM devices WHERE id = ?", (new_id,)).fetchone()
            old_full = c.execute("SELECT * FROM devices WHERE id = ?", (old["id"],)).fetchone()
        self.push_device(old_full)      # מסיר את הישן מהפרוקסי
        self.push_device(new)
        log.info("transferred device %s → %s at %s", old["id"], new_id, ip)
        return {"device_id": new_id, "token": new_token, "status": "active", "level": old["level"],
                "level_name": LEVEL_NAMES.get(old["level"], str(old["level"])),
                "vpn": self.vpn_config(ip, psk), "ca": self.ca_info()}

    # ------------------------------------------------------------ שחזור לפי מספר טלפון / החלפת מכשיר

    @staticmethod
    def clean_uid(raw) -> str:
        """מזהה המכשיר מהטלפון: hash (hex) של ANDROID_ID — יציב בהסרה והתקנה מחדש, משתנה באיפוס יצרן
        או בטלפון אחר. לא מידע מזהה בעצמו. ריק אם לא תקין."""
        uid = str(raw or "").strip().lower()
        return uid if _UID_RE.match(uid) else ""

    def _customer_by_phone(self, c: sqlite3.Connection, phone: str) -> sqlite3.Row:
        phone_n = security.normalize_phone(phone)
        if not phone_n:
            raise ApiError(400, "מספר הטלפון לא תקין")
        cust = c.execute("SELECT * FROM customers WHERE phone = ? ORDER BY id LIMIT 1", (phone_n,)).fetchone()
        if cust is None:
            raise ApiError(404, "לא נמצא חשבון עם המספר הזה. אפשר להירשם כלקוח חדש.")
        return cust

    def recognize(self, device_uid: str) -> dict:
        """זיהוי מכשיר חוזר לפי המזהה האמיתי של אנדרואיד (device_uid), בלי מספר טלפון — נקרא עם
        פתיחת האפליקציה אחרי התקנה מחדש. אותו מזהה קיים במאגר → האפליקציה מציעה מיד שחזור (הלקוח
        מאמת עם מספר טלפון). לא קיים (איפוס / טלפון חדש) → זרימת הרשמה/שחזור הרגילה.
        מחזיר מינימום (בלי מספר טלפון): האם מוכר והמצב — כדי לנתב את המסך."""
        uid = self.clean_uid(device_uid)
        if not uid:
            return {"known": False}
        with self.db() as c:
            rows = c.execute(
                "SELECT status, disabled_reason, name FROM devices WHERE device_uid = ? AND status != 'rejected'"
                " ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'disabled' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,"
                " id DESC", (uid,)).fetchall()
        if not rows:
            return {"known": False}
        d = rows[0]
        # הושבת על ידי המנהל (לא "הוסר על ידי הלקוח") — לא מציעים שחזור אוטומטי
        if d["status"] == "disabled" and d["disabled_reason"] != "uninstalled":
            return {"known": True, "status": "admin_disabled", "device_name": d["name"]}
        if d["status"] == "pending":
            return {"known": True, "status": "pending", "device_name": d["name"]}
        return {"known": True, "status": "restorable", "device_name": d["name"]}

    def restore_lookup(self, *, phone: str, device_uid: str, public_key: str, name: str, platform: str,
                       app_version: str) -> dict:
        """הלקוח הזין מספר טלפון במסך הפתיחה (אחרי הסרה והתקנה מחדש, איפוס או טלפון חדש):
          - אותו טלפון (אותו מזהה מכשיר) → ההגנה חוזרת מיד: מפתח ואסימון חדשים, אותה כתובת ומדיניות.
          - טלפון אחר → מחזירים את המכשירים בחשבון, והאפליקציה שואלת "להעביר את ההגנה לטלפון הזה?"
            (או מכשיר נוסף, בתשלום) — וזה הולך לאישור המנהל (restore_request).
        מכשיר שהמנהל השבית לא חוזר לבד."""
        uid = self.clean_uid(device_uid)
        if not uid:
            raise ApiError(400, "מזהה המכשיר חסר — עדכנו את האפליקציה")
        if not security.is_wg_key(public_key):
            raise ApiError(400, "מפתח ציבורי לא תקין")
        with self.db() as c, db.transaction(c):
            cust = self._customer_by_phone(c, phone)
            devices = c.execute("SELECT * FROM devices WHERE customer_id = ? AND status != 'rejected' ORDER BY id",
                                (cust["id"],)).fetchall()
            same = next((d for d in devices if d["device_uid"] == uid and d["vpn_ip"] and (
                d["status"] == "active" or (d["status"] == "disabled" and d["disabled_reason"] == "uninstalled"))),
                None)
            if same is None:
                pending_same = next((d for d in devices if d["device_uid"] == uid and d["status"] == "pending"), None)
                if pending_same is not None:
                    return {"status": "pending", "message": "כבר נשלחה בקשה מהטלפון הזה — היא ממתינה לאישור."}
                admin_off = next((d for d in devices if d["device_uid"] == uid and d["status"] == "disabled"), None)
                if admin_off is not None:
                    raise ApiError(403, "ההגנה בטלפון הזה הושבתה על ידי המנהל. פנו לתמיכה.")
                return {"status": "needs_choice", "customer_name": cust["name"],
                        "devices": [{"id": d["id"], "name": d["name"], "status": d["status"],
                                     "last_seen_at": d["last_seen_at"]}
                                    for d in devices if d["status"] in ("active", "disabled")],
                        "message": "לחשבון הזה יש הגנה על טלפון אחר."}
            if c.execute("SELECT 1 FROM devices WHERE public_key = ? AND id != ?", (public_key, same["id"])).fetchone():
                raise ApiError(409, "המכשיר כבר רשום")
            token = security.new_token()
            psk = security.new_preshared_key()
            c.execute("UPDATE devices SET public_key = ?, preshared_key = ?, token_hash = ?, status = 'active',"
                      " disabled_reason = '', name = ?, platform = ?, app_version = ?, pause_until = NULL,"
                      " unmanage_at = NULL WHERE id = ?",
                      (public_key, psk, security.token_hash(token), (name or same["name"]).strip()[:60] or same["name"],
                       platform[:20], app_version[:20], same["id"]))
            self.log_device(c, same["id"], "restored", "הלקוח שחזר את ההגנה לפי מספר הטלפון (אותו טלפון)")
            self.audit(f"customer:{cust['id']}", "restore", f"device:{same['id']}", "same device_uid", c)
            if same["public_key"] and same["public_key"] != public_key:
                try:
                    self.wg.remove_peer(same["public_key"])
                except WireGuardError as e:
                    log.warning("old peer removal failed (reconcile will fix): %s", e)
            self.wg.add_peer(public_key, psk, same["vpn_ip"])
            fresh = c.execute("SELECT * FROM devices WHERE id = ?", (same["id"],)).fetchone()
        self.push_device(fresh)
        log.info("device %s restored by phone (customer %s)", fresh["id"], cust["id"])
        return {"status": "active", "device_id": fresh["id"], "token": token, "level": fresh["level"],
                "level_name": LEVEL_NAMES.get(fresh["level"], str(fresh["level"])),
                "vpn": self.vpn_config(fresh["vpn_ip"], psk), "ca": self.ca_info()}

    def restore_request(self, *, phone: str, device_uid: str, public_key: str, name: str, platform: str,
                        app_version: str, mode: str, old_device_id: int | None) -> dict:
        """טלפון חדש לחשבון קיים: "העברת ההגנה לטלפון הזה" (swap — הישן יתנתק) או "מכשיר נוסף"
        (additional — חשבון נוסף בתשלום). נוצר מכשיר ממתין, והמנהל מאשר בדף הבקשות."""
        if mode not in ("swap", "additional"):
            raise ApiError(400, "סוג הבקשה לא תקין")
        uid = self.clean_uid(device_uid)
        if not uid:
            raise ApiError(400, "מזהה המכשיר חסר — עדכנו את האפליקציה")
        if not security.is_wg_key(public_key):
            raise ApiError(400, "מפתח ציבורי לא תקין")
        token = security.new_token()
        with self.db() as c, db.transaction(c):
            cust = self._customer_by_phone(c, phone)
            if c.execute("SELECT 1 FROM devices WHERE public_key = ?", (public_key,)).fetchone():
                raise ApiError(409, "המכשיר כבר רשום")
            if c.execute("SELECT 1 FROM devices WHERE customer_id = ? AND device_uid = ? AND status = 'pending'",
                         (cust["id"], uid)).fetchone():
                raise ApiError(409, "כבר נשלחה בקשה מהטלפון הזה — היא ממתינה לאישור")
            source = None
            if old_device_id is not None:
                source = c.execute("SELECT * FROM devices WHERE id = ? AND customer_id = ?",
                                   (old_device_id, cust["id"])).fetchone()
                if source is None:
                    raise ApiError(404, "המכשיר הקודם לא נמצא בחשבון")
            if mode == "swap" and source is None:
                raise ApiError(400, "בחרו איזה טלפון מוחלף")
            if source is None:      # מכשיר נוסף: מתחילים מהמדיניות של המכשיר האחרון בחשבון
                source = c.execute("SELECT * FROM devices WHERE customer_id = ? AND status IN ('active', 'disabled')"
                                   " ORDER BY id DESC LIMIT 1", (cust["id"],)).fetchone()
            t = db.now()
            dev_name = security.clean_text(name, 60) or cust["name"]
            new_id = c.execute(
                "INSERT INTO devices (name, label, platform, app_version, public_key, level, status, token_hash,"
                " customer_id, policy, device_uid, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (dev_name, cust["name"], platform[:20], app_version[:20], public_key,
                 source["level"] if source else 2, "pending", security.token_hash(token), cust["id"],
                 source["policy"] if source else DeviceSettings().dump(), uid, t)).lastrowid
            swap_id = c.execute("INSERT INTO swaps (customer_id, new_device_id, old_device_id, mode, created_at)"
                                " VALUES (?,?,?,?,?)",
                                (cust["id"], new_id, source["id"] if mode == "swap" else None, mode, t)).lastrowid
            self.bump_policy(c, new_id)
            self.audit(f"device:{new_id}", f"{mode}_request", f"swap:{swap_id}", f"customer:{cust['id']}", c)
        log.info("%s request %s for customer %s (new device %s)", mode, swap_id, cust["id"], new_id)
        msg = ("הבקשה להעברת ההגנה לטלפון הזה נשלחה למנהל. ברגע שיאשר — ההגנה תעבור לכאן והטלפון הקודם יתנתק."
               if mode == "swap" else "הבקשה למכשיר נוסף נשלחה למנהל. נעדכן כאן ברגע שיאושר.")
        return {"device_id": new_id, "token": token, "status": "pending", "swap_id": swap_id, "message": msg}

    def list_swaps(self, status: str = "pending") -> list[dict]:
        with self.db() as c:
            rows = c.execute(
                "SELECT s.*, cu.name AS customer_name, cu.phone AS customer_phone,"
                " n.name AS new_name, n.platform AS new_platform, o.name AS old_name, o.status AS old_status,"
                " o.last_seen_at AS old_last_seen FROM swaps s JOIN customers cu ON cu.id = s.customer_id"
                " JOIN devices n ON n.id = s.new_device_id LEFT JOIN devices o ON o.id = s.old_device_id"
                " WHERE s.status = ? ORDER BY s.id DESC LIMIT 200", (status,)).fetchall()
        return [dict(r) for r in rows]

    def resolve_swap(self, swap_id: int, approve: bool, admin: str, as_additional: bool = False) -> dict:
        """אישור החלפה: הטלפון החדש פעיל עם הלקוח, הרמה והמדיניות; הישן מתנתק (מושבת, "הוחלף").
        אישור כמכשיר נוסף: החדש פעיל, הישן ממשיך. דחייה: החדש נדחה."""
        with self.db() as c, db.transaction(c):
            sw = c.execute("SELECT * FROM swaps WHERE id = ?", (swap_id,)).fetchone()
            if sw is None:
                raise ApiError(404, "הבקשה לא נמצאה")
            if sw["status"] != "pending":
                raise ApiError(409, "הבקשה כבר טופלה")
            new = c.execute("SELECT * FROM devices WHERE id = ?", (sw["new_device_id"],)).fetchone()
            if new is None or new["status"] != "pending":
                raise ApiError(409, "המכשיר החדש כבר לא ממתין")
            t = db.now()
            if not approve:
                c.execute("UPDATE devices SET status = 'rejected' WHERE id = ?", (new["id"],))
                c.execute("UPDATE swaps SET status = 'rejected', resolved_at = ?, resolved_by = ? WHERE id = ?",
                          (t, admin[:60], swap_id))
                self.audit(admin, "swap_reject", f"swap:{swap_id}", "", c)
                return {"ok": True, "status": "rejected"}
            ip = self._allocate_ip(c)
            psk = security.new_preshared_key()
            c.execute("UPDATE devices SET status = 'active', vpn_ip = ?, preshared_key = ?, approved_at = ?"
                      " WHERE id = ?", (ip, psk, t, new["id"]))
            old = None
            swap = sw["mode"] == "swap" and not as_additional and sw["old_device_id"]
            if swap:
                old = c.execute("SELECT * FROM devices WHERE id = ?", (sw["old_device_id"],)).fetchone()
                if old is not None:
                    c.execute("UPDATE devices SET status = 'disabled', disabled_reason = 'transferred' WHERE id = ?",
                              (old["id"],))
                    self.log_device(c, old["id"], "transferred", f"ההגנה עברה לטלפון חדש (מכשיר {new['id']})")
                    self.log_device(c, new["id"], "transfer_in", f"← מכשיר {old['id']}")
            c.execute("UPDATE swaps SET status = 'approved', resolved_at = ?, resolved_by = ?, mode = ? WHERE id = ?",
                      (t, admin[:60], "swap" if swap else "additional", swap_id))
            self.bump_policy(c, new["id"])
            self.audit(admin, "swap_approve", f"swap:{swap_id}",
                       f"{'swap' if swap else 'additional'} new:{new['id']} old:{old['id'] if old else '-'}", c)
            self.wg.add_peer(new["public_key"], psk, ip)
            if old is not None and old["public_key"]:
                try:
                    self.wg.remove_peer(old["public_key"])
                except WireGuardError as e:
                    log.warning("old peer removal failed (reconcile will fix): %s", e)
            fresh_new = c.execute("SELECT * FROM devices WHERE id = ?", (new["id"],)).fetchone()
            fresh_old = c.execute("SELECT * FROM devices WHERE id = ?", (old["id"],)).fetchone() if old else None
        self.push_device(fresh_new)
        if fresh_old is not None:
            self.push_device(fresh_old)
        return {"ok": True, "status": "approved", "mode": "swap" if swap else "additional",
                "device_id": fresh_new["id"]}

    # ------------------------------------------------------------ הרשמה מהאפליקציה

    def register(self, *, name: str, phone: str, email: str, level: int, options: list[str], note: str,
                 public_key: str, device_name: str, platform: str, app_version: str, device_uid: str = "") -> dict:
        """בקשת הצטרפות: לקוח + מכשיר "ממתין" (בלי VPN). המנהל מאשר אחרי תשלום."""
        if not self.cfg.register_enabled:
            raise ApiError(403, "ההרשמה סגורה כרגע. פנו אלינו לקבלת קוד הפעלה.")
        if not security.is_wg_key(public_key):
            raise ApiError(400, "מפתח ציבורי לא תקין")
        name = security.clean_text(name, 60)
        if len(name) < 2:
            raise ApiError(400, "נא למלא שם מלא")
        phone_n = security.normalize_phone(phone)
        if not phone_n:
            raise ApiError(400, "מספר הטלפון לא תקין")
        email_n = security.normalize_email(email)
        if email and not email_n:
            raise ApiError(400, "כתובת המייל לא תקינה")
        if level not in (1, 2, 3):
            raise ApiError(400, "נא לבחור רמת סינון")
        opts = sorted({o for o in options if o in CATALOG})
        note = security.clean_text(note, 1500, multiline=True)
        token = security.new_token()
        with self.db() as c, db.transaction(c):
            pending = c.execute("SELECT count(*) FROM requests WHERE status = 'pending'").fetchone()[0]
            if pending >= self.cfg.max_pending:
                raise ApiError(503, "יש כרגע עומס בקשות. נסו שוב מאוחר יותר.")
            if c.execute("SELECT 1 FROM devices WHERE public_key = ?", (public_key,)).fetchone():
                raise ApiError(409, "המכשיר כבר רשום")
            t = db.now()
            cust = c.execute("SELECT id FROM customers WHERE phone = ? ORDER BY id LIMIT 1", (phone_n,)).fetchone()
            if cust is None:
                customer_id = c.execute(
                    "INSERT INTO customers (name, phone, email, created_at) VALUES (?,?,?,?)",
                    (name, phone_n, email_n, t)).lastrowid
            else:
                customer_id = cust["id"]
                if email_n:
                    c.execute("UPDATE customers SET email = ? WHERE id = ? AND email = ''", (email_n, customer_id))
            dev_name = security.clean_text(device_name, 60) or name
            device_id = c.execute(
                "INSERT INTO devices (name, label, platform, app_version, public_key, level, status, token_hash,"
                " customer_id, policy, device_uid, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (dev_name, name, platform[:20], app_version[:20], public_key, level, "pending",
                 security.token_hash(token), customer_id, DeviceSettings(options=opts).dump(),
                 self.clean_uid(device_uid), t)).lastrowid
            request_id = c.execute(
                "INSERT INTO requests (kind, device_id, customer_id, level, options, note, created_at)"
                " VALUES ('join',?,?,?,?,?,?)",
                (device_id, customer_id, level, json.dumps(opts), note, t)).lastrowid
            self.bump_policy(c, device_id)
            self.audit(f"device:{device_id}", "register", f"request:{request_id}", f"{name} {phone_n}", c)
        log.info("registration request %s from %s (device %s)", request_id, mask_phone(phone_n), device_id)
        return {"device_id": device_id, "token": token, "status": "pending", "request_id": request_id,
                "message": STATUS_MESSAGES["pending"]}

    # ------------------------------------------------------------ מכשיר (מהאפליקציה)

    def device_by_token(self, token: str) -> sqlite3.Row:
        with self.db() as c:
            row = c.execute("SELECT * FROM devices WHERE token_hash = ?",
                            (security.token_hash(token),)).fetchone()
            if row is None:
                raise ApiError(401, "המכשיר לא מזוהה")
            t = db.now()
            # כתיבה למסד לכל היותר פעם בדקה למכשיר (טלפון פתוח שואל כל 20 שניות)
            if not row["last_seen_at"] or t - row["last_seen_at"] >= LAST_SEEN_EVERY_S:
                c.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (t, row["id"]))
            return row

    def device_status(self, row: sqlite3.Row, client_ip: str = "") -> dict:
        out: dict = {
            "device_id": row["id"], "name": row["name"], "status": row["status"],
            "level": row["level"], "level_name": LEVEL_NAMES.get(row["level"], ""),
            "server_time": db.now(), "message": STATUS_MESSAGES.get(row["status"], ""),
        }
        with self.db() as c:
            req = c.execute("SELECT id, kind, status, admin_note, created_at, resolved_at FROM requests"
                            " WHERE device_id = ? ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
            customer = None
            if row["customer_id"]:
                customer = c.execute(
                    "SELECT id, name, phone, email, payment, paid_until FROM customers WHERE id = ?",
                    (row["customer_id"],),
                ).fetchone()
        if customer is not None:
            out["customer"] = dict(customer)
        else:
            out["customer"] = None
        if req is not None:
            out["request"] = {"id": req["id"], "kind": req["kind"], "status": req["status"],
                              "created_at": req["created_at"], "resolved_at": req["resolved_at"]}
            if row["status"] == "rejected" and req["admin_note"]:
                out["message"] = f"{STATUS_MESSAGES['rejected']} {req['admin_note']}"
        if row["status"] != "active":
            return out
        settings = DeviceSettings.load(row["policy"])
        comp = compile_settings(row["level"], settings)
        today = self.device_stats(row["vpn_ip"])
        out.update({
            "vpn_ip": row["vpn_ip"],
            # הבקשה הגיעה מכתובת ה-VPN של המכשיר = התעבורה באמת עוברת במנהרה ובסינון
            "via_vpn": client_ip == row["vpn_ip"],
            "today": {"blocked": today.get("block", 0), "painted": today.get("replace", 0),
                      "sites": today.get("site", 0)},
            "blocks": summary(settings),
            "device_policy": {**comp.device_json(), "version": row["policy_version"],
                              "updated_at": row["policy_updated_at"], "checksum": row["policy_checksum"],
                              "revision": row["policy_revision"]},
            "apps_report_needed": not row["apps_at"] or db.now() - row["apps_at"] > 86400,
            "managed_apps": self.device_managed_apps(),
            "store_categories": store_seed()["categories"],     # סדר הטאבים בחנות
            "heartbeat_interval_s": self.cfg.heartbeat_interval_s,
        })
        return out

    # ------------------------------------------------------------ דיווח תקופתי (heartbeat)

    def heartbeat(self, row: sqlite3.Row, raw: dict, client_ip: str = "") -> dict:
        """הטלפון מדווח כל ~15 דקות (גם כשהאפליקציה סגורה): מצב ה-VPN, הנגישות, מנהל המכשיר,
        הסוללה וגרסת המדיניות שהוחלה. התשובה: הגרסה העדכנית — והמדיניות עצמה אם הטלפון מאחור."""
        t = db.now()
        hb = sanitize_heartbeat(raw)
        if row["vpn_ip"]:
            hb["via_vpn"] = client_ip == row["vpn_ip"]
        prev = _json_dict(row["heartbeat"])
        acked = hb.get("policy_version")
        with self.db() as c, db.transaction(c):
            cur = c.execute("SELECT policy_version, managed_at FROM devices WHERE id = ?", (row["id"],)).fetchone()
            if cur is None:
                raise ApiError(401, "המכשיר לא מזוהה")
            sets = {"heartbeat": json.dumps(hb, ensure_ascii=False), "heartbeat_at": t, "last_seen_at": t}
            if (uid := self.clean_uid(raw.get("device_uid"))) and uid != row["device_uid"]:
                sets["device_uid"] = uid          # מכשירים ותיקים: המזהה נרשם בדיווח הראשון אחרי העדכון
            if isinstance(hb.get("app_version"), str):
                sets["app_version"] = hb["app_version"][:20]
            if isinstance(acked, int):
                sets["device_policy_version"] = min(acked, cur["policy_version"])
                if acked >= cur["policy_version"]:
                    sets["policy_ack_at"] = t
            if hb.get("device_owner") is True and not cur["managed_at"]:
                sets["managed_at"] = t
            full = c.execute("SELECT unmanage_at FROM devices WHERE id = ?", (row["id"],)).fetchone()
            if full["unmanage_at"] and hb.get("device_owner") is False:   # הטלפון אישר שהניהול הוסר
                sets["unmanage_at"] = None
                self.log_device(c, row["id"], "management_cleared")
            c.execute(f"UPDATE devices SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                      (*sets.values(), row["id"]))
            if prev:
                for path, good, up, down in _LOG_TRANSITIONS:
                    before, after = _dig(prev, path), _dig(hb, path)
                    if after is None or before is None or (before == good) == (after == good):
                        continue
                    self.log_device(c, row["id"], up if after == good else down)
            if prev.get("via_vpn") is not False and hb.get("via_vpn") is False:
                self.log_device(c, row["id"], "outside_vpn", "דיווח שהגיע שלא דרך המנהרה")
            self._log_data_checks(c, row["id"], prev.get("vpn") or {}, hb.get("vpn") or {})
            if hb.get("policy_error") and hb.get("policy_error") != prev.get("policy_error"):
                self.log_device(c, row["id"], "policy_error", hb["policy_error"])
            if prev.get("app_version") and hb.get("app_version") and prev["app_version"] != hb["app_version"]:
                self.log_device(c, row["id"], "app_updated", f"{prev['app_version']} → {hb['app_version']}")
            err = _dig(hb, ("update", "error"))
            if err and err != _dig(prev, ("update", "error")):
                self.log_device(c, row["id"], "app_update_failed", f"{_dig(hb, ('update', 'version')) or ''} {err}")
            # השהיה שפג תוקפה בין דיווחים — מנקים ומשחזרים את מדיניות הפרוקסי המלאה
            cur_pause = c.execute("SELECT pause_until FROM devices WHERE id = ?", (row["id"],)).fetchone()
            pause_expired = bool(prev.get("_was_paused")) and not self.is_paused(cur_pause, t)
            if pause_expired:
                c.execute("UPDATE devices SET pause_until = NULL, pause_reason = '', pause_nonce = '' WHERE id = ?",
                          (row["id"],))
                self.log_device(c, row["id"], "resumed", "פג תוקף ההשהיה")
            fresh = c.execute("SELECT * FROM devices WHERE id = ?", (row["id"],)).fetchone()
            if self.is_paused(fresh, t):
                hb["_was_paused"] = True      # נשמר ב-heartbeat כדי לזהות פקיעה בדיווח הבא
                c.execute("UPDATE devices SET heartbeat = ? WHERE id = ?",
                          (json.dumps(hb, ensure_ascii=False), row["id"]))
        if pause_expired:
            self.push_device(fresh)           # פג התוקף → סינון מלא חוזר
        out = {"server_time": t, "status": fresh["status"], "policy_version": fresh["policy_version"],
               "interval_s": self.cfg.heartbeat_interval_s, "message": STATUS_MESSAGES.get(fresh["status"], "")}
        admin_cmd = self._admin_command(fresh, t)
        if admin_cmd:
            out["admin"] = admin_cmd
        if fresh["status"] == "active" and (acked != fresh["policy_version"] or raw.get("want_policy")):
            out["policy"] = self.device_policy(fresh)
        if fresh["status"] == "active" and (release := self.app_release()):
            out["app_update"] = release     # הטלפון מחליט: עדכון שקט במנהל מכשיר, אחרת הודעה בלבד
        if fresh["status"] == "active":
            out["managed_apps"] = self.device_managed_apps()   # קטלוג מאושר; required יותקנו אוטומטית במנהל מכשיר
        return out

    def _log_data_checks(self, c: sqlite3.Connection, device_id: int, prev: dict, vpn: dict) -> None:
        """בדיקות התעבורה של הטלפון: "המנהרה למעלה בלי אינטרנט" נרשם ביומן המכשיר (עם השלב שנכשל
        וכמה פעמים מאז הדיווח הקודם), וכשהתעבורה חוזרת — גם זה. הדיווח יומי / בפתיחת האפליקציה, ולכן
        מונה הכשלים (המצטבר בטלפון) הוא מה שמראה כשלים שקרו בין דיווחים."""
        fails, before = vpn.get("data_fails"), prev.get("data_fails")
        new_fails = fails - before if isinstance(fails, int) and isinstance(before, int) and fails > before else 0
        if new_fails or (vpn.get("data_ok") is False and prev.get("data_ok") is not False):
            age = vpn.get("data_fail_age_s")
            when = f", לפני {age // 60} דק'" if isinstance(age, int) else ""
            times = f" ×{new_fails}" if new_fails > 1 else ""
            self.log_device(c, device_id, "vpn_no_data",
                            f"מנהרה למעלה בלי תעבורה: {vpn.get('data_error') or '?'}{times}{when}")
        if vpn.get("data_ok") is True and prev.get("data_ok") is False:
            self.log_device(c, device_id, "vpn_data_ok", "התעבורה במנהרה חזרה")

    def _admin_command(self, row: sqlite3.Row, t: int) -> dict | None:
        """פקודת מנהל שמועברת לטלפון בערוץ המאומת (heartbeat): השהיה או הסרת ניהול. התוקף מגיע
        מהשרת; הטלפון מאשר לפי ה-nonce (anti-replay) ומשחזר לבד בפקיעה."""
        if row["unmanage_at"]:
            return {"action": "unmanage", "nonce": f"u{row['unmanage_at']}"}
        if self.is_paused(row, t):
            return {"action": "pause", "nonce": row["pause_nonce"], "until": row["pause_until"],
                    "reason": row["pause_reason"]}
        return None

    def device_vpn(self, row: sqlite3.Row) -> dict:
        if row["status"] != "active" or not row["vpn_ip"]:
            raise ApiError(403, STATUS_MESSAGES.get(row["status"], "המכשיר לא פעיל"))
        return {**self.vpn_config(row["vpn_ip"], row["preshared_key"]), "ca": self.ca_info()}

    def device_request_change(self, row: sqlite3.Row, level: int | None, options: list[str] | None,
                              note: str) -> dict:
        """לקוח פעיל מבקש שינוי (רמה / חסימות). רק המנהל מבצע — כאן רק נרשמת בקשה."""
        if row["status"] != "active":
            raise ApiError(403, "אפשר לבקש שינוי רק במכשיר פעיל")
        if level is not None and level not in (1, 2, 3):
            raise ApiError(400, "רמה לא חוקית")
        opts = sorted({o for o in (options or []) if o in CATALOG})
        note = security.clean_text(note, 1500, multiline=True)
        if level is None and options is None and not note:
            raise ApiError(400, "הבקשה ריקה")
        with self.db() as c, db.transaction(c):
            old = c.execute("SELECT id FROM requests WHERE device_id = ? AND kind = 'change' AND status = 'pending'",
                            (row["id"],)).fetchone()
            if old is not None:     # בקשה פתוחה — מעדכנים אותה במקום לפתוח עוד אחת
                c.execute("UPDATE requests SET level = ?, options = ?, note = ?, created_at = ? WHERE id = ?",
                          (level, json.dumps(opts), note, db.now(), old["id"]))
                request_id = old["id"]
            else:
                request_id = c.execute(
                    "INSERT INTO requests (kind, device_id, customer_id, level, options, note, created_at)"
                    " VALUES ('change',?,?,?,?,?,?)",
                    (row["id"], row["customer_id"], level, json.dumps(opts), note, db.now())).lastrowid
            self.audit(f"device:{row['id']}", "request_change", f"request:{request_id}", note[:100], c)
        return {"request_id": request_id, "status": "pending"}

    def device_cancel(self, row: sqlite3.Row) -> None:
        """ביטול הרשמה שעוד לא אושרה (מהאפליקציה). מכשיר פעיל לא יכול למחוק את עצמו."""
        if row["status"] not in ("pending", "rejected"):
            raise ApiError(403, "אפשר לבטל רק בקשה שעוד לא אושרה")
        with self.db() as c, db.transaction(c):
            c.execute("UPDATE requests SET status = 'cancelled', resolved_at = ? WHERE device_id = ?"
                      " AND status = 'pending'", (db.now(), row["id"]))
            c.execute("DELETE FROM devices WHERE id = ?", (row["id"],))
            self.audit(f"device:{row['id']}", "cancel", f"device:{row['id']}", row["name"], c)

    def report_device(self, row: sqlite3.Row, apps: list[dict] | None, guard: dict | None) -> dict:
        """הטלפון מדווח: אילו אפליקציות מותקנות (כדי שהמנהל יוכל לחסום כל אחת), ומצב ההגנה."""
        sets, args = [], []
        if apps is not None:
            clean = []
            for a in apps[:MAX_REPORTED_APPS]:
                pkg = str(a.get("package", ""))[:200]
                if not pkg or not all(ch.isalnum() or ch in "._" for ch in pkg):
                    continue
                clean.append({"package": pkg, "label": security.clean_text(str(a.get("label", "")), 80),
                              "system": bool(a.get("system", False))})
            sets += ["apps = ?", "apps_at = ?"]
            args += [json.dumps(sorted(clean, key=lambda x: x["label"].lower()), ensure_ascii=False), db.now()]
        if guard is not None:
            allowed = ("accessibility", "device_owner", "device_admin", "always_on", "lockdown", "ca_installed",
                       "private_dns", "battery_unrestricted", "app_version")
            g = {k: guard[k] for k in allowed if k in guard and isinstance(guard[k], (bool, str, int))}
            sets += ["guard = ?", "guard_at = ?"]
            args += [json.dumps(g), db.now()]
        if sets:
            with self.db() as c:
                c.execute(f"UPDATE devices SET {', '.join(sets)} WHERE id = ?", (*args, row["id"]))
        return {"ok": True}

    # ------------------------------------------------------------ בקשות (מנהל)

    def list_requests(self, status: str | None = "pending", limit: int = 200) -> list[dict]:
        sql = ("SELECT r.*, d.name AS device_name, d.platform, d.app_version, d.status AS device_status,"
               " d.level AS device_level, d.policy AS device_policy,"
               " c.name AS customer_name, c.phone, c.email, c.payment, c.paid_until,"
               " (SELECT count(*) FROM devices d2 WHERE d2.customer_id = r.customer_id) AS customer_devices"
               " FROM requests r LEFT JOIN devices d ON d.id = r.device_id"
               " LEFT JOIN customers c ON c.id = r.customer_id")
        args: tuple = ()
        if status:
            sql += " WHERE r.status = ?"
            args = (status,)
        sql += " ORDER BY r.id DESC LIMIT ?"
        with self.db() as c:
            rows = c.execute(sql, (*args, max(1, min(limit, 1000)))).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["options"] = [{"id": o, "title": CATALOG[o].title, "category": CATALOG[o].category}
                            for o in _json_list(d["options"]) if o in CATALOG]
            d["level_name"] = LEVEL_NAMES.get(d["level"] or 0, "")
            d["current"] = DeviceSettings.load(d.pop("device_policy")).as_dict() if d["kind"] == "change" else None
            out.append(d)
        return out

    def approve_request(self, request_id: int, admin: str, decision: dict) -> dict:
        """אישור: בהצטרפות — פתיחת החיבור (כתובת VPN, רמה, חסימות); בשינוי — עדכון המכשיר.

        decision: level, options, custom_hosts, allow_hosts, custom_apps, app_images, payment, paid_until,
                  admin_note — מה שהמנהל קבע בפועל (ברירת מחדל: מה שהלקוח ביקש).
        """
        with self.db() as c, db.transaction(c):
            req = c.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
            if req is None:
                raise ApiError(404, "בקשה לא נמצאה")
            if req["status"] != "pending":
                raise ApiError(409, "הבקשה כבר טופלה")
            dev = c.execute("SELECT * FROM devices WHERE id = ?", (req["device_id"],)).fetchone()
            if dev is None:
                raise ApiError(404, "המכשיר של הבקשה לא קיים יותר")
            level = decision.get("level")
            level = req["level"] if level is None else level
            level = dev["level"] if level is None else level
            if level not in LEVEL_NAMES:
                raise ApiError(400, "רמה לא חוקית")
            settings = DeviceSettings.load(dev["policy"])
            if decision.get("options") is None:
                decision = {**decision, "options": _json_list(req["options"])
                            if req["kind"] == "join" else settings.options}
            errors = settings.update(decision)
            if errors:
                raise ApiError(400, "; ".join(errors))
            t = db.now()
            if req["kind"] == "join":
                if dev["status"] != "pending":
                    raise ApiError(409, "המכשיר כבר לא ממתין לאישור")
                ip = self._allocate_ip(c)
                psk = security.new_preshared_key()
                c.execute("UPDATE devices SET status = 'active', vpn_ip = ?, preshared_key = ?, level = ?,"
                          " policy = ?, approved_at = ? WHERE id = ?",
                          (ip, psk, level, settings.dump(), t, dev["id"]))
            else:
                c.execute("UPDATE devices SET level = ?, policy = ? WHERE id = ?",
                          (level, settings.dump(), dev["id"]))
            c.execute("UPDATE requests SET status = 'approved', resolved_at = ?, admin_note = ? WHERE id = ?",
                      (t, security.clean_text(decision.get("admin_note") or "", 500), request_id))
            if req["customer_id"]:
                self._update_customer(c, req["customer_id"], decision)
            version = self.bump_policy(c, dev["id"])
            self.audit(admin, "approve", f"request:{request_id}",
                       f"{req['kind']} device:{dev['id']} level:{level} options:{','.join(settings.options)}"
                       f" policy:v{version}", c)
            new = c.execute("SELECT * FROM devices WHERE id = ?", (dev["id"],)).fetchone()
            if req["kind"] == "join":
                # אם WireGuard נכשל — הכול מתבטל, והבקשה נשארת ממתינה
                self.wg.add_peer(new["public_key"], new["preshared_key"], new["vpn_ip"])
        self.push_device(new)
        log.info("request %s approved by %s (device %s, level %s)", request_id, admin, new["id"], level)
        return {"device_id": new["id"], "status": new["status"], "vpn_ip": new["vpn_ip"]}

    def reject_request(self, request_id: int, admin: str, note: str) -> None:
        with self.db() as c, db.transaction(c):
            req = c.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
            if req is None:
                raise ApiError(404, "בקשה לא נמצאה")
            if req["status"] != "pending":
                raise ApiError(409, "הבקשה כבר טופלה")
            c.execute("UPDATE requests SET status = 'rejected', resolved_at = ?, admin_note = ? WHERE id = ?",
                      (db.now(), security.clean_text(note, 500), request_id))
            if req["kind"] == "join":
                c.execute("UPDATE devices SET status = 'rejected' WHERE id = ? AND status = 'pending'",
                          (req["device_id"],))
            self.audit(admin, "reject", f"request:{request_id}", note[:200], c)

    # ------------------------------------------------------------ לקוחות

    @staticmethod
    def _update_customer(c: sqlite3.Connection, customer_id: int, changes: dict) -> None:
        sets, args = [], []
        for key, limit in (("name", 60), ("notes", 2000)):
            if changes.get(key) is not None:
                sets.append(f"{key} = ?")
                args.append(security.clean_text(changes[key], limit, multiline=key == "notes"))
        if changes.get("phone") is not None:
            phone = security.normalize_phone(changes["phone"])
            if not phone:
                raise ApiError(400, "מספר הטלפון לא תקין")
            sets.append("phone = ?")
            args.append(phone)
        if changes.get("email") is not None:
            email = security.normalize_email(changes["email"]) if changes["email"] else ""
            if changes["email"] and not email:
                raise ApiError(400, "כתובת המייל לא תקינה")
            sets.append("email = ?")
            args.append(email)
        if changes.get("payment") is not None:
            if changes["payment"] not in ("unpaid", "paid", "free"):
                raise ApiError(400, "סטטוס תשלום לא חוקי")
            sets.append("payment = ?")
            args.append(changes["payment"])
        if "paid_until" in changes:
            sets.append("paid_until = ?")
            args.append(int(changes["paid_until"]) if changes["paid_until"] else None)
        if sets:
            c.execute(f"UPDATE customers SET {', '.join(sets)} WHERE id = ?", (*args, customer_id))

    def update_customer(self, customer_id: int, changes: dict, admin: str) -> dict:
        with self.db() as c, db.transaction(c):
            if c.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone() is None:
                raise ApiError(404, "לקוח לא נמצא")
            self._update_customer(c, customer_id, changes)
            self.audit(admin, "customer", f"customer:{customer_id}",
                       ", ".join(k for k, v in changes.items() if v is not None), c)
            return dict(c.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone())

    def list_customers(self) -> list[dict]:
        with self.db() as c:
            rows = c.execute(
                "SELECT c.*, (SELECT count(*) FROM devices d WHERE d.customer_id = c.id) AS devices,"
                " (SELECT count(*) FROM devices d WHERE d.customer_id = c.id AND d.status = 'active') AS active"
                " FROM customers c ORDER BY c.id DESC").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ מכשירים (מנהל)

    def compliance_of(self, r: sqlite3.Row, handshake: int, t: int | None = None) -> compliance.Compliance:
        comp = self.compiled(r)
        return compliance.evaluate(
            r, _json_dict(r["heartbeat"]), _json_dict(r["guard"]), handshake,
            needs_device_block=bool(comp.apps), needs_accessibility=comp.needs_accessibility,
            now=t or db.now(), offline_after_s=self.cfg.offline_after_s, lost_after_s=self.cfg.lost_after_s,
            sync_grace_s=self.cfg.sync_grace_s)

    def _device_row_out(self, r: sqlite3.Row, peers: dict, stats: dict, t: int) -> dict:
        p = peers.get(r["public_key"])
        hs = p.latest_handshake if p else 0
        settings = DeviceSettings.load(r["policy"])
        guard = _json_dict(r["guard"])
        hb = _json_dict(r["heartbeat"])
        return {
            "compliance": self.compliance_of(r, hs, t).as_dict(),
            "policy_version": r["policy_version"], "device_policy_version": r["device_policy_version"],
            "policy_updated_at": r["policy_updated_at"], "policy_ack_at": r["policy_ack_at"],
            "heartbeat": hb, "heartbeat_at": r["heartbeat_at"], "managed_at": r["managed_at"],
            "id": r["id"], "name": r["name"], "label": r["label"], "platform": r["platform"],
            "app_version": r["app_version"], "vpn_ip": r["vpn_ip"], "level": r["level"],
            "status": r["status"], "legacy": bool(r["legacy"]), "created_at": r["created_at"],
            "approved_at": r["approved_at"], "customer_id": r["customer_id"],
            "last_seen_at": r["last_seen_at"], "last_handshake": hs or None,
            "online": bool(hs and t - hs < ONLINE_WINDOW_S),
            "rx": p.rx if p else 0, "tx": p.tx if p else 0,
            "today": stats.get(r["vpn_ip"], {}) if r["vpn_ip"] else {},
            "blocks": summary(settings), "app_images": settings.app_images,
            "guard": guard, "guard_at": r["guard_at"],
        }

    def list_devices(self) -> list[dict]:
        try:
            peers = self.wg.peers()
        except WireGuardError:
            peers = {}
        stats = self.daily_stats()
        t = db.now()
        with self.db() as c:
            rows = c.execute("SELECT * FROM devices ORDER BY id DESC").fetchall()
            customers = {r["id"]: dict(r) for r in c.execute("SELECT * FROM customers")}
        out = []
        for r in rows:
            d = self._device_row_out(r, peers, stats, t)
            cust = customers.get(r["customer_id"])
            d["customer"] = {k: cust[k] for k in ("id", "name", "phone", "email", "payment", "paid_until")} \
                if cust else None
            out.append(d)
        return out

    def device_row(self, device_id: int) -> sqlite3.Row:
        with self.db() as c:
            r = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        if r is None:
            raise ApiError(404, "מכשיר לא נמצא")
        return r

    def device_detail(self, device_id: int) -> dict:
        with self.db() as c:
            r = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if r is None:
                raise ApiError(404, "מכשיר לא נמצא")
            cust = c.execute("SELECT * FROM customers WHERE id = ?", (r["customer_id"],)).fetchone() \
                if r["customer_id"] else None
            reqs = c.execute("SELECT * FROM requests WHERE device_id = ? ORDER BY id DESC LIMIT 20",
                             (device_id,)).fetchall()
        try:
            peers = self.wg.peers()
        except WireGuardError:
            peers = {}
        out = self._device_row_out(r, peers, self.daily_stats(), db.now())
        settings = DeviceSettings.load(r["policy"])
        comp = compile_settings(r["level"], settings)
        blocked = set(comp.apps) | set(comp.split_apps)
        out.update({
            "settings": settings.as_dict(),
            "known_app_hosts": {p: list(KNOWN_APP_HOSTS.get(p, ())) for p in
                                {a["package"] for a in _json_list(r["apps"])} | set(settings.app_rules)
                                if p in KNOWN_APP_HOSTS},
            "compiled": {"hosts": len(comp.hosts), "apps": len(comp.apps), "images": comp.images,
                         "video": comp.video, "app_images": comp.app_images,
                         "needs_guard": any(CATALOG[o].needs_guard for o in settings.options)
                         or any(r["mode"] != "network" for r in settings.app_rules.values()),
                         "protections": list(comp.protections), "dpc": comp.dpc,
                         "split_apps": list(comp.split_apps), "vpn_bypass": comp.vpn_bypass},
            "customer": dict(cust) if cust else None,
            "requests": [{**dict(q), "options": _json_list(q["options"])} for q in reqs],
            "apps": [{**a, "blocked": a["package"] in blocked} for a in _json_list(r["apps"])],
            "apps_at": r["apps_at"],
            "history": self.stats_range(7, r["vpn_ip"]) if r["vpn_ip"] else [],
            "events": self.recent_events(50, ip=r["vpn_ip"]) if r["vpn_ip"] else [],
            "device_log": self.device_log(device_id, 40),
            "policy": self.device_policy(r),
            "network_only_unknown": list(comp.network_only_unknown),
            "app_rules": [{"package": p, **rule} for p, rule in settings.app_rules.items()],
            "latest_app_version": (self.app_release() or {}).get("version_name"),
            "pause": {"active": self.is_paused(r), "until": r["pause_until"], "reason": r["pause_reason"],
                      "by": r["pause_by"]} if r["pause_until"] else None,
            "unmanage_requested": bool(r["unmanage_at"]),
        })
        return out

    def update_device(self, device_id: int, changes: dict, admin: str = "admin") -> dict:
        allowed = {k: v for k, v in changes.items() if k in {"name", "label", "level", "status"} and v is not None}
        if "level" in allowed and allowed["level"] not in LEVEL_NAMES:
            raise ApiError(400, "רמה לא חוקית")
        if "status" in allowed and allowed["status"] not in ("active", "disabled"):
            raise ApiError(400, "סטטוס לא חוקי")
        policy_keys = ("options", "custom_hosts", "allow_hosts", "custom_apps", "app_rules", "app_images", "dpc",
                       "keywords", "vpn_bypass")
        policy_changes = {k: changes[k] for k in policy_keys if changes.get(k) is not None}
        with self.db() as c, db.transaction(c):
            row = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if row is None:
                raise ApiError(404, "מכשיר לא נמצא")
            if "status" in allowed and row["status"] in ("pending", "rejected"):
                raise ApiError(409, "מכשיר שממתין לאישור מופעל דרך 'בקשות'")
            if policy_changes:
                settings = DeviceSettings.load(row["policy"])
                errors = settings.update(policy_changes)
                if errors:
                    raise ApiError(400, "; ".join(errors))
                allowed["policy"] = settings.dump()
            if "status" in allowed:     # השבתה ע"י המנהל לא "חוזרת לבד" בשחזור לפי מספר טלפון
                allowed["disabled_reason"] = "admin" if allowed["status"] == "disabled" else ""
            if allowed:
                sets = ", ".join(f"{k} = ?" for k in allowed)
                c.execute(f"UPDATE devices SET {sets} WHERE id = ?", (*allowed.values(), device_id))
            self.bump_policy(c, device_id)
            new = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if new["status"] != row["status"]:
                if new["status"] == "active":
                    self.wg.add_peer(new["public_key"], new["preshared_key"], new["vpn_ip"])
                else:
                    self.wg.remove_peer(new["public_key"])
            what = [k for k in allowed if k != "policy"] + list(policy_changes)
            self.audit(admin, "device", f"device:{device_id}", ", ".join(
                f"{k}={allowed[k]}" if k in ("level", "status") else k for k in what), c)
        self.push_device(new)
        return dict(new)

    def pause_device(self, device_id: int, minutes: int, reason: str, admin: str) -> dict:
        """השהיית הגנה מאושרת (מצב תחזוקה): עד [minutes] דקות. סינון התמונות בפרוקסי כבוי, ובטלפון
        המגן ונעילת ה-VPN מורפים — עד שהתוקף פג, ואז הכול חוזר לבד. פעולה רגישה — נרשמת ב-audit."""
        minutes = max(1, min(int(minutes), MAX_PAUSE_MINUTES))
        reason = security.clean_text(reason, 200, multiline=True)
        t = db.now()
        with self.db() as c, db.transaction(c):
            row = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if row is None:
                raise ApiError(404, "מכשיר לא נמצא")
            if row["status"] != "active":
                raise ApiError(409, "אפשר להשהות רק מכשיר פעיל")
            nonce = secrets.token_hex(8)
            c.execute("UPDATE devices SET pause_until = ?, pause_reason = ?, pause_by = ?, pause_nonce = ? "
                      "WHERE id = ?", (t + minutes * 60, reason, admin[:60], nonce, device_id))
            self.log_device(c, device_id, "paused", f"{minutes} דק' · {reason}"[:200])
            self.audit(admin, "pause", f"device:{device_id}", f"{minutes}m nonce={nonce} {reason}", c)
            new = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        self.push_device(new)      # פרוקסי → passthrough מיידי
        return self.device_detail(device_id)

    def resume_device(self, device_id: int, admin: str) -> dict:
        """ביטול ההשהיה מיד (לפני שהתוקף פג) — ההגנה המלאה חוזרת."""
        with self.db() as c, db.transaction(c):
            row = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if row is None:
                raise ApiError(404, "מכשיר לא נמצא")
            was_paused = self.is_paused(row)
            c.execute("UPDATE devices SET pause_until = NULL, pause_reason = '', pause_nonce = '' WHERE id = ?",
                      (device_id,))
            if was_paused:
                self.log_device(c, device_id, "resumed")
                self.audit(admin, "resume", f"device:{device_id}", "", c)
            new = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        self.push_device(new)      # פרוקסי → מדיניות מלאה שוב
        return self.device_detail(device_id)

    def device_resume(self, row: sqlite3.Row) -> dict:
        """הלקוח לחץ "החזר הגנה" בזמן השהיה: גם בשרת ההשהיה מסתיימת מיד (אחרת הפרוקסי היה נשאר
        "שקוף" עד סוף התוקף). תמיד מותר — זה רק מחזיר הגנה."""
        with self.db() as c, db.transaction(c):
            cur = c.execute("SELECT * FROM devices WHERE id = ?", (row["id"],)).fetchone()
            was_paused = self.is_paused(cur)
            c.execute("UPDATE devices SET pause_until = NULL, pause_reason = '', pause_nonce = '' WHERE id = ?",
                      (row["id"],))
            if was_paused:
                self.log_device(c, row["id"], "resumed", "הלקוח החזיר את ההגנה מהטלפון")
                self.audit(f"device:{row['id']}", "resume", f"device:{row['id']}", "from phone", c)
            new = c.execute("SELECT * FROM devices WHERE id = ?", (row["id"],)).fetchone()
        self.push_device(new)
        return {"ok": True, "was_paused": was_paused}

    def device_uninstalled(self, row: sqlite3.Row) -> dict:
        """הלקוח בחר "הסרת הכול" בזמן השהיה מאושרת: הטלפון מסיר את הניהול, ה-VPN, התעודה והאפליקציה.
        בשרת — המכשיר מושבת (הכתובת והאסימון לא עובדים יותר) ונרשם ביומן. התקנה מחדש = שחזור לפי
        מספר הטלפון. בלי השהיה בתוקף — נדחה ונרשם כניסיון עקיפה."""
        t = db.now()
        with self.db() as c, db.transaction(c):
            cur = c.execute("SELECT * FROM devices WHERE id = ?", (row["id"],)).fetchone()
            if not self.is_paused(cur, t):
                self.log_device(c, row["id"], "uninstall_blocked", "ניסיון הסרה בלי השהיה מאושרת")
                raise ApiError(409, "הסרה אפשרית רק בזמן השהיה שהמנהל אישר")
            c.execute("UPDATE devices SET status = 'disabled', disabled_reason = 'uninstalled', pause_until = NULL,"
                      " pause_reason = '', pause_nonce = '', token_hash = NULL WHERE id = ?", (row["id"],))
            self.log_device(c, row["id"], "uninstalled", "הלקוח הסיר את ההגנה מהטלפון (בזמן השהיה)")
            self.audit(f"device:{row['id']}", "uninstall", f"device:{row['id']}", "during authorized pause", c)
            new = c.execute("SELECT * FROM devices WHERE id = ?", (row["id"],)).fetchone()
        self.wg.remove_peer(new["public_key"])
        self.push_device(new)
        return {"ok": True}

    def request_unmanage(self, device_id: int, admin: str) -> dict:
        """בקשה מאושרת להסרת ניהול: הטלפון יסיר את עצמו כ-Device Owner בדיווח הבא (clearDeviceOwnerApp).
        לא מוחק את המכשיר — רק משחרר ניהול, כדי שאפשר יהיה להסיר/להעביר. נרשם ב-audit."""
        with self.db() as c, db.transaction(c):
            row = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if row is None:
                raise ApiError(404, "מכשיר לא נמצא")
            c.execute("UPDATE devices SET unmanage_at = ? WHERE id = ?", (db.now(), device_id))
            self.log_device(c, device_id, "unmanage_requested")
            self.audit(admin, "unmanage", f"device:{device_id}", "", c)
        return self.device_detail(device_id)

    # ------------------------------------------------------------ פרופילים מוכנים

    def seed_profiles(self) -> None:
        """פרופילי ברירת מחדל (פעם אחת, אם הטבלה ריקה). המנהל יכול לערוך; את המובנים אי אפשר למחוק."""
        with self.db() as c, db.transaction(c):
            if c.execute("SELECT 1 FROM profiles LIMIT 1").fetchone():
                return
            t = db.now()
            for name, level, opts, extra in SEED_PROFILES:
                s = DeviceSettings.load(json.dumps({"options": opts, **extra}))
                c.execute("INSERT INTO profiles (name, level, settings, builtin, created_at, updated_at) "
                          "VALUES (?,?,?,1,?,?)", (name, level, s.dump(), t, t))
        log.info("seeded %d built-in profiles", len(SEED_PROFILES))

    def seed_store(self) -> None:
        """מזריע את החנות לפי גרסאות (store_seed_version ב-meta), כל גרסה פעם אחת:
        1 — FilterTube; 2 — קטלוג אפליקציות בסיסיות מאומתות מ-Google Play (store_seed.json).
        מה שהמנהל ערך או מחק אחרי הזריעה — לא נדרס (מעדכנים רק שדות שעוד שווים לערך המקורי)."""
        with self.db() as c, db.transaction(c):
            row = c.execute("SELECT value FROM meta WHERE key = 'store_seed_version'").fetchone()
            have = int(row["value"]) if row else (
                1 if c.execute("SELECT 1 FROM meta WHERE key = 'store_seeded'").fetchone() else 0)
            if have >= STORE_SEED_VERSION:
                return
            if have < 1:
                self._seed_store_v1(c)
            if have < 2:
                self._seed_store_v2(c)
            if have < 3:
                self._seed_store_v3(c)
            c.execute("INSERT INTO meta (key, value) VALUES ('store_seed_version', ?)"
                      " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(STORE_SEED_VERSION),))
        log.info("store seeded up to version %d", STORE_SEED_VERSION)

    @staticmethod
    def _seed_store_v1(c: sqlite3.Connection) -> None:
        t = db.now()
        for a in _FT_V1:
            if c.execute("SELECT 1 FROM managed_apps WHERE package = ?", (a["package"],)).fetchone():
                continue
            c.execute(
                "INSERT INTO managed_apps (package, label, category, note, description, play_url, sort,"
                " screenshots, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (a["package"], a["label"], a["category"], a["note"], FT_DESC, a.get("play_url", ""),
                 a["sort"], "[]", t, t))

    @staticmethod
    def _seed_store_v2(c: sqlite3.Connection) -> None:
        seed = store_seed()
        t = db.now()
        # FilterTube: ב-Google Play נמצאת com.filtertube.app; com.filtertube.filtertube — התקנה ישירה בלבד.
        # הקטגוריה "וידאו" → "מדיה" (הטאב בחנות). מעדכנים רק מה שעוד זהה לזריעה של גרסה 1.
        c.execute("UPDATE managed_apps SET category = 'מדיה' WHERE package IN (?, ?) AND category = 'וידאו'",
                  (FT_STORE, FT_FULL))
        ft = seed["filtertube_play"]
        c.execute("UPDATE managed_apps SET play_url = ?, label = CASE WHEN label = ? THEN ? ELSE label END,"
                  " note = CASE WHEN note = ? THEN ? ELSE note END,"
                  " icon_url = CASE WHEN icon_url = '' THEN ? ELSE icon_url END,"
                  " screenshots = CASE WHEN screenshots IN ('', '[]') THEN ? ELSE screenshots END, updated_at = ?"
                  " WHERE package = ? AND play_url = ''",
                  (f"https://play.google.com/store/apps/details?id={FT_FULL}", "FilterTube", "FilterTube",
                   _FT_V1[1]["note"], "יוטיוב כשר עם 3 רמות סינון — מ-Google Play.",
                   ft["icon_url"], json.dumps(ft["screenshots"]), t, FT_FULL))
        c.execute("UPDATE managed_apps SET play_url = '',"
                  " label = CASE WHEN label = ? THEN ? ELSE label END,"
                  " note = CASE WHEN note = ? THEN ? ELSE note END, updated_at = ?"
                  " WHERE package = ? AND play_url = ?",
                  (_FT_V1[0]["label"], "FilterTube — הגרסה המלאה", _FT_V1[0]["note"],
                   "יוטיוב כשר עם 3 רמות סינון — התקנה ישירה (לא דרך Google Play).", t, FT_STORE,
                   _FT_V1[0]["play_url"]))
        for a in seed["apps"]:
            if c.execute("SELECT 1 FROM managed_apps WHERE package = ?", (a["package"],)).fetchone():
                continue
            c.execute(
                "INSERT INTO managed_apps (package, label, category, note, description, icon_url, screenshots,"
                " play_url, sort, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (a["package"], a["label"], a["category"], a["note"], a["description"], a["icon_url"],
                 json.dumps(a["screenshots"]), a["play_url"], a["sort"], t, t))

    @staticmethod
    def _seed_store_v3(c: sqlite3.Connection) -> None:
        """FilterTube חוזרת להיות הגרסה הפרוצה של המנהל — מתארחת אצלנו ומותקנת ישירות (לא מ-Google Play).
        גרסה 2 קישרה אותה בטעות ל-Google Play (אותו שם חבילה — com.filtertube.app). מנקים את הקישור
        ואת תמונות ה-Play, ומאחדים לכרטיס אחד. את ה-APK הפרוץ המנהל מעלה בדשבורד. לא דורסים עריכות ידניות."""
        t = db.now()
        play = f"https://play.google.com/store/apps/details?id={FT_FULL}"
        # com.filtertube.app: מנתקים מ-Google Play וממירים לכרטיס "התקנה ישירה" (רק אם עדיין זהה לזריעת v2)
        c.execute(
            "UPDATE managed_apps SET play_url = '',"
            " icon_url = CASE WHEN icon_url = ? THEN '' ELSE icon_url END,"
            " screenshots = CASE WHEN screenshots = ? THEN '[]' ELSE screenshots END,"
            " label = CASE WHEN label = 'FilterTube' THEN ? ELSE label END,"
            " note = CASE WHEN note = ? THEN ? ELSE note END, updated_at = ?"
            " WHERE package = ? AND play_url = ?",
            (store_seed()["filtertube_play"]["icon_url"],
             json.dumps(store_seed()["filtertube_play"]["screenshots"]),
             "FilterTube — יוטיוב כשר", "יוטיוב כשר עם 3 רמות סינון — מ-Google Play.", FT_NOTE,
             t, FT_FULL, play))
        # com.filtertube.filtertube: הכרטיס הכפול של v2 — מוחקים רק אם לא נערך ולא הועלה אליו APK
        c.execute(
            "DELETE FROM managed_apps WHERE package = ? AND play_url = '' AND (apk_url IS NULL OR apk_url = '')"
            " AND label = 'FilterTube — הגרסה המלאה'", (FT_STORE,))

    # ------------------------------------------------------------ תמונות החנות — דרך מסנן התמונות

    def store_media(self, app_id: int, slot: str) -> tuple[bytes, str]:
        """אייקון / צילום מסך של אפליקציה בחנות, אחרי מנוע הסינון ברמה המחמירה (1) — בלי קשר לרמת
        המכשיר. נשמר בדיסק (לפי hash של הכתובת). מנוע לא זמין → 503: לא מגישים תמונה שלא סוננה."""
        with self.db() as c:
            row = c.execute("SELECT icon_url, screenshots FROM managed_apps WHERE id = ? AND enabled = 1",
                            (app_id,)).fetchone()
        if row is None:
            raise ApiError(404, "לא נמצא")
        urls = _json_list(row["screenshots"])
        m = re.fullmatch(r"s([0-7])", slot)
        url = row["icon_url"] if slot == "icon" else urls[int(m.group(1))] if m and int(m.group(1)) < len(urls) else ""
        if not url:
            raise ApiError(404, "לא נמצא")
        folder = Path(self.cfg.data_dir) / "store_media"
        key = hashlib.sha256(url.encode()).hexdigest()
        body_f, ct_f = folder / key, folder / f"{key}.ct"
        try:
            if time.time() - body_f.stat().st_mtime < STORE_MEDIA_TTL_S:
                return body_f.read_bytes(), ct_f.read_text()
        except OSError:
            pass
        try:
            data, ct = fetch_image(url, STORE_MEDIA_MAX_BYTES)
        except FetchError as e:
            raise ApiError(502, str(e)) from e
        try:
            header, filtered = protocol.call_sync(self.cfg.engine_socket, {"op": "classify", "level": 1,
                                                                           "budget_ms": 15000}, data, timeout=20)
        except (OSError, protocol.ProtocolError) as e:
            raise ApiError(503, "מנוע הסינון לא זמין") from e
        if header.get("action") == "pass":
            out, out_ct = data, ct if ct.startswith("image/") else "image/jpeg"
        elif filtered:
            out, out_ct = filtered, header.get("ct") or "image/png"
        else:
            raise ApiError(503, "מנוע הסינון לא החזיר תמונה")
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{key}.tmp"
        tmp.write_bytes(out)
        ct_f.write_text(out_ct)
        os.replace(tmp, body_f)
        return out, out_ct

    def _media_url(self, app_id: int, slot: str, version: int) -> str:
        return f"https://{self.cfg.public_host}/v1/store/media/{app_id}/{slot}?v={version}"

    def list_profiles(self) -> list[dict]:
        with self.db() as c:
            rows = c.execute("SELECT * FROM profiles ORDER BY builtin DESC, name").fetchall()
        out = []
        for r in rows:
            s = DeviceSettings.load(r["settings"])
            out.append({"id": r["id"], "name": r["name"], "level": r["level"], "builtin": bool(r["builtin"]),
                        "settings": s.as_dict(), "summary": summary(s), "updated_at": r["updated_at"]})
        return out

    def save_profile(self, admin: str, name: str, level: int, settings: dict,
                     profile_id: int | None = None) -> dict:
        name = security.clean_text(name, 60)
        if not name:
            raise ApiError(400, "שם פרופיל חסר")
        if level not in LEVEL_NAMES:
            raise ApiError(400, "רמה לא חוקית")
        s = DeviceSettings.load("{}")
        errors = s.update(settings or {})
        if errors:
            raise ApiError(400, "; ".join(errors))
        t = db.now()
        with self.db() as c, db.transaction(c):
            if profile_id is None:
                if c.execute("SELECT 1 FROM profiles WHERE name = ?", (name,)).fetchone():
                    raise ApiError(409, "כבר קיים פרופיל בשם הזה")
                cur = c.execute("INSERT INTO profiles (name, level, settings, created_at, updated_at) "
                                "VALUES (?,?,?,?,?)", (name, level, s.dump(), t, t))
                profile_id = cur.lastrowid
                self.audit(admin, "profile_create", f"profile:{profile_id}", name, c)
            else:
                if c.execute("SELECT 1 FROM profiles WHERE id = ?", (profile_id,)).fetchone() is None:
                    raise ApiError(404, "פרופיל לא נמצא")
                dup = c.execute("SELECT 1 FROM profiles WHERE name = ? AND id != ?", (name, profile_id)).fetchone()
                if dup:
                    raise ApiError(409, "כבר קיים פרופיל בשם הזה")
                c.execute("UPDATE profiles SET name = ?, level = ?, settings = ?, updated_at = ? WHERE id = ?",
                          (name, level, s.dump(), t, profile_id))
                self.audit(admin, "profile_update", f"profile:{profile_id}", name, c)
        return {"id": profile_id, "name": name}

    def delete_profile(self, profile_id: int, admin: str) -> None:
        with self.db() as c, db.transaction(c):
            row = c.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
            if row is None:
                raise ApiError(404, "פרופיל לא נמצא")
            if row["builtin"]:
                raise ApiError(409, "אי אפשר למחוק פרופיל מובנה")
            c.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
            self.audit(admin, "profile_delete", f"profile:{profile_id}", row["name"], c)

    def apply_profile(self, device_id: int, profile_id: int, admin: str) -> dict:
        """מחיל פרופיל על מכשיר: מחליף את הרמה והמדיניות, יוצר גרסה חדשה, ומסנכרן. נרשם ב-audit."""
        with self.db() as c, db.transaction(c):
            dev = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if dev is None:
                raise ApiError(404, "מכשיר לא נמצא")
            prof = c.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
            if prof is None:
                raise ApiError(404, "פרופיל לא נמצא")
            s = DeviceSettings.load(prof["settings"])
            c.execute("UPDATE devices SET level = ?, policy = ? WHERE id = ?",
                      (prof["level"], s.dump(), device_id))
            self.bump_policy(c, device_id)
            self.log_device(c, device_id, "profile_applied", prof["name"])
            self.audit(admin, "profile_apply", f"device:{device_id}", prof["name"], c)
            new = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        self.push_device(new)
        return self.device_detail(device_id)

    # ------------------------------------------------------------ קטלוג אפליקציות מאושרות

    _SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

    def managed_apps(self, enabled_only: bool = False) -> list[dict]:
        with self.db() as c:
            q = "SELECT * FROM managed_apps"
            if enabled_only:
                q += " WHERE enabled = 1"
            q += " ORDER BY sort, label, package"
            return [dict(r) for r in c.execute(q).fetchall()]

    def device_managed_apps(self) -> list[dict]:
        """מה שהטלפון מקבל: רק אפליקציות מאושרות, עם שדות ההתקנה (בלי נתוני ניהול פנימיים)."""
        out = []
        for a in self.managed_apps(enabled_only=True):
            # התמונות דרך השרת — אחרי מסנן התמונות (store_media), לא ישירות מהמקור
            shots = _json_list(a["screenshots"])
            entry = {"package": a["package"], "label": a["label"], "category": a["category"],
                     "note": a["note"], "description": a["description"],
                     "icon_url": self._media_url(a["id"], "icon", a["updated_at"]) if a["icon_url"] else "",
                     "screenshots": [self._media_url(a["id"], f"s{i}", a["updated_at"]) for i in range(len(shots))],
                     "play_url": a["play_url"],
                     # השם כפי שמופיע ב-Google Play (עברית/אנגלית) — לנעילת חלון Play לאפליקציה הזו
                     "play_titles": _play_titles().get(a["package"], []),
                     "required": bool(a["required"]), "added_at": a["created_at"],
                     "updated_at": a["updated_at"]}
            if a["apk_url"] and self._SHA256_RE.match(a["apk_sha256"] or "") and a["version_code"]:
                entry.update(url=a["apk_url"], sha256=a["apk_sha256"], version_code=a["version_code"],
                             version_name=a["version_name"])
            out.append(entry)
        return out

    def save_managed_app(self, admin: str, data: dict, app_id: int | None = None) -> dict:
        pkg = clean_package(str(data.get("package", "")))
        if not pkg:
            raise ApiError(400, "שם חבילה לא תקין")
        url = str(data.get("apk_url", "")).strip()
        sha = str(data.get("apk_sha256", "")).strip().lower()
        if url and not url.startswith("https://"):
            raise ApiError(400, "כתובת ה-APK חייבת להיות https")
        if url and not self._SHA256_RE.match(sha):
            raise ApiError(400, "להתקנה אוטומטית צריך SHA-256 תקין (64 תווי hex) של קובץ ה-APK")
        vc = data.get("version_code")
        vc = int(vc) if isinstance(vc, int | float) and not isinstance(vc, bool) and 0 < vc < 2**31 else None
        play = str(data.get("play_url", "")).strip()
        if play and not re.match(r"^https://play\.google\.com/", play):
            raise ApiError(400, "קישור Play חייב להתחיל ב-https://play.google.com/")
        icon = str(data.get("icon_url", "")).strip()
        if icon and not icon.startswith("https://"):
            raise ApiError(400, "כתובת האייקון חייבת להיות https")
        shots = [s for s in (data.get("screenshots") or []) if isinstance(s, str) and s.startswith("https://")][:8]
        fields = {
            "package": pkg, "label": security.clean_text(data.get("label"), 60) or pkg,
            "category": security.clean_text(data.get("category"), 40),
            "note": security.clean_text(data.get("note"), 200, multiline=True),
            "description": security.clean_text(data.get("description"), 2000, multiline=True),
            "screenshots": json.dumps(shots), "icon_url": icon, "play_url": play,
            "apk_url": url, "apk_sha256": sha if url else "", "version_code": vc if url else None,
            "required": 1 if data.get("required") else 0, "enabled": 0 if data.get("enabled") is False else 1,
            "sort": int(data.get("sort")) if isinstance(data.get("sort"), int) else 0,
        }
        t = db.now()
        with self.db() as c, db.transaction(c):
            if app_id is None:
                if c.execute("SELECT 1 FROM managed_apps WHERE package = ?", (pkg,)).fetchone():
                    raise ApiError(409, "האפליקציה כבר בקטלוג")
                cols = ", ".join(fields) + ", created_at, updated_at"
                cur = c.execute(f"INSERT INTO managed_apps ({cols}) VALUES ({', '.join('?' * (len(fields) + 2))})",
                                (*fields.values(), t, t))
                app_id = cur.lastrowid
                self.audit(admin, "app_add", f"app:{app_id}", pkg, c)
            else:
                if c.execute("SELECT 1 FROM managed_apps WHERE id = ?", (app_id,)).fetchone() is None:
                    raise ApiError(404, "אפליקציה לא נמצאה")
                dup = c.execute("SELECT 1 FROM managed_apps WHERE package = ? AND id != ?", (pkg, app_id)).fetchone()
                if dup:
                    raise ApiError(409, "האפליקציה כבר בקטלוג")
                c.execute(f"UPDATE managed_apps SET {', '.join(f'{k} = ?' for k in fields)}, updated_at = ? "
                          "WHERE id = ?", (*fields.values(), t, app_id))
                self.audit(admin, "app_update", f"app:{app_id}", pkg, c)
        return {"id": app_id, "package": pkg}

    APK_KEEP = 2                        # כמה גרסאות לשמור לכל אפליקציה (הנוכחית + הקודמת)

    def apps_dir(self) -> Path:
        return Path(self.cfg.data_dir) / "apps"

    def store_apk(self, admin: str, tmp: Path, sha256: str) -> dict:
        """המנהל העלה APK: קוראים ממנו את שם החבילה והגרסה, שומרים אותו אצלנו (https, עם SHA-256),
        ומוסיפים/מעדכנים בקטלוג. טלפונים במנהל מכשיר יוכלו להתקין ולעדכן גם כשההתקנות חסומות."""
        try:
            info = read_apk(str(tmp))
        except ApkError as e:
            tmp.unlink(missing_ok=True)
            raise ApiError(400, str(e)) from e
        pkg = clean_package(info.package)
        if not pkg or pkg == "com.filterphone.app":
            tmp.unlink(missing_ok=True)
            raise ApiError(400, "לא ניתן להוסיף את האפליקציה הזו לחנות")
        name = f"{pkg}-{info.version_code}.apk"
        dest = self.apps_dir() / name
        os.replace(tmp, dest)
        dest.chmod(0o644)
        old = sorted(self.apps_dir().glob(f"{pkg}-*.apk"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in old[self.APK_KEEP:]:
            f.unlink(missing_ok=True)
        url = f"https://{self.cfg.public_host}/v1/apps/{name}"
        t = db.now()
        with self.db() as c, db.transaction(c):
            row = c.execute("SELECT * FROM managed_apps WHERE package = ?", (pkg,)).fetchone()
            if row is None:
                app_id = c.execute(
                    "INSERT INTO managed_apps (package, label, apk_url, apk_sha256, version_code, version_name,"
                    " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (pkg, security.clean_text(info.label, 60) or pkg, url, sha256, info.version_code,
                     security.clean_text(info.version_name, 40), t, t)).lastrowid
                self.audit(admin, "app_upload", f"app:{app_id}", f"{pkg} {info.version_code} new", c)
            else:
                app_id = row["id"]
                c.execute("UPDATE managed_apps SET apk_url = ?, apk_sha256 = ?, version_code = ?, version_name = ?,"
                          " updated_at = ? WHERE id = ?",
                          (url, sha256, info.version_code, security.clean_text(info.version_name, 40), t, app_id))
                self.audit(admin, "app_upload", f"app:{app_id}", f"{pkg} {info.version_code}", c)
        log.info("apk uploaded: %s %s (%s)", pkg, info.version_code, sha256[:12])
        return {"id": app_id, "package": pkg, "label": info.label, "version_code": info.version_code,
                "version_name": info.version_name, "sha256": sha256, "url": url}

    def delete_managed_app(self, app_id: int, admin: str) -> None:
        with self.db() as c, db.transaction(c):
            row = c.execute("SELECT package FROM managed_apps WHERE id = ?", (app_id,)).fetchone()
            if row is None:
                raise ApiError(404, "אפליקציה לא נמצאה")
            c.execute("DELETE FROM managed_apps WHERE id = ?", (app_id,))
            self.audit(admin, "app_delete", f"app:{app_id}", row["package"], c)

    def delete_device(self, device_id: int, admin: str = "admin") -> None:
        with self.db() as c, db.transaction(c):
            row = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if row is None:
                raise ApiError(404, "מכשיר לא נמצא")
            c.execute("DELETE FROM devices WHERE id = ?", (device_id,))
            self.audit(admin, "delete", f"device:{device_id}", row["name"], c)
            if row["vpn_ip"]:
                try:
                    self.wg.remove_peer(row["public_key"])
                except WireGuardError as e:
                    log.warning("remove peer failed (continuing): %s", e)
        if row["vpn_ip"]:
            try:
                pipe = self.redis.pipeline()
                pipe.hdel(LEVELS_KEY, row["vpn_ip"])
                pipe.hdel(POLICY_KEY, row["vpn_ip"])
                pipe.incr(POLICY_REV_KEY)
                pipe.execute()
            except redis.RedisError as e:
                log.error("redis cleanup failed for deleted device %s: %s", device_id, e)
                self.redis_stale = True
            self.clear_passthrough(row["vpn_ip"])
        self.dirty.set()

    def clear_passthrough(self, ip: str) -> int:
        try:
            fields = [f for f in self.redis.hkeys(PASSTHROUGH_KEY) if f.startswith(ip + "|")]
            if fields:
                self.redis.hdel(PASSTHROUGH_KEY, *fields)
            return len(fields)
        except redis.RedisError:
            return 0

    def learned_passthrough(self, ip: str) -> list[dict]:
        try:
            now = time.time()
            return sorted(({"host": f.split("|", 1)[1], "expires_at": int(float(v))}
                           for f, v in self.redis.hgetall(PASSTHROUGH_KEY).items()
                           if f.startswith(ip + "|") and float(v) > now), key=lambda x: x["host"])
        except redis.RedisError:
            return []

    # ------------------------------------------------------------ קודי הפעלה

    def create_code(self, label: str, level: int, uses: int, expires_days: int | None,
                    options: list[str] | None = None) -> dict:
        if level not in LEVEL_NAMES:
            raise ApiError(400, "רמה לא חוקית")
        uses = max(1, min(int(uses), 100))
        opts = sorted({o for o in (options or []) if o in CATALOG})   # ברירת המחדל נקבעת בשכבת ה-API
        code = security.new_code()
        expires = db.now() + int(expires_days) * 86400 if expires_days else None
        with self.db() as c:
            cur = c.execute(
                "INSERT INTO codes (code_hash, hint, label, level, uses_left, expires_at, created_at, options)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (security.code_hash(code), code[-3:], label.strip()[:80], level, uses, expires, db.now(),
                 json.dumps(opts)))
        return {"id": cur.lastrowid, "code": code, "label": label, "level": level,
                "uses_left": uses, "expires_at": expires, "options": opts}

    def list_codes(self) -> list[dict]:
        with self.db() as c:
            rows = c.execute("SELECT id, hint, label, level, uses_left, expires_at, created_at FROM codes"
                             " ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

    def delete_code(self, code_id: int) -> None:
        with self.db() as c:
            if c.execute("DELETE FROM codes WHERE id = ?", (code_id,)).rowcount == 0:
                raise ApiError(404, "קוד לא נמצא")

    # ------------------------------------------------------------ גרסת האפליקציה שפורסמה

    _release_cache: tuple[tuple, dict | None] = ((), None)
    _release_lock = threading.Lock()

    def app_release(self) -> dict | None:
        """הגרסה האחרונה בדף ההורדה, לפי FilterPhone.json: לכל קובץ — versionCode, מעבדים, גודל
        ו-SHA-256. ה-SHA-256 נבדק כאן מול הקובץ עצמו (פעם אחת לכל שינוי): קובץ שלא תואם לרשום
        לא מוצע — עדיף לא לעדכן מאשר לשלוח לטלפונים משהו אחר ממה שנבנה."""
        root = Path(self.cfg.apk_dir)
        try:
            st = (root / RELEASE_MANIFEST).stat()
        except OSError:
            return None
        with self._release_lock:
            try:
                raw = json.loads((root / RELEASE_MANIFEST).read_text(encoding="utf-8"))
                entries = [f for f in raw.get("files", []) if isinstance(f, dict)
                           and isinstance(f.get("name"), str) and _RELEASE_NAME.match(f["name"])]
                stats = {f["name"]: (root / f["name"]).stat() for f in entries if (root / f["name"]).is_file()}
            except (OSError, ValueError, AttributeError):
                log.warning("app release manifest unreadable")
                return None
            key = (st.st_mtime_ns, st.st_size, *sorted((n, s.st_mtime_ns, s.st_size) for n, s in stats.items()))
            if self._release_cache[0] == key:
                return self._release_cache[1]
            files = []
            for f in entries:
                code, sha, size = f.get("version_code"), str(f.get("sha256", "")).lower(), f.get("size")
                fst = stats.get(f["name"])
                if (fst is None or not isinstance(code, int) or isinstance(code, bool) or not 0 < code < 2**31
                        or not _SHA256_HEX.match(sha) or size != fst.st_size or not 0 < fst.st_size <= MAX_APK_BYTES):
                    log.warning("app release: skipping %s (missing / invalid entry)", f["name"])
                    continue
                if self._sha256_hex(root / f["name"]) != sha:
                    log.warning("app release: %s does not match FilterPhone.json — not offered", f["name"])
                    continue
                abis = [a for a in f.get("abis", []) if isinstance(a, str) and _ABI.match(a)][:8] \
                    if isinstance(f.get("abis"), list) else []
                files.append({"url": f"https://{self.cfg.public_host}/download/{f['name']}", "version_code": code,
                              "abis": abis, "sha256": sha, "size": size})
            name = raw.get("version_name") if isinstance(raw, dict) else None
            release = {"version_name": security.clean_text(name, 20) if isinstance(name, str) else "",
                       "files": files} if files else None
            self._release_cache = (key, release)
            return release

    @staticmethod
    def _sha256_hex(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    # ------------------------------------------------------------ הקמת Device Owner ב-QR

    _apk_cache: tuple[tuple, str] = ((), "")

    def _apk_checksum(self, path: Path) -> str:
        """SHA-256 של קובץ ה-APK, ב-base64 בטוח לכתובות (כמו שאנדרואיד מצפה). נשמר לפי זמן שינוי."""
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        if self._apk_cache[0] == key:
            return self._apk_cache[1]
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        value = base64.urlsafe_b64encode(h.digest()).decode().rstrip("=")
        self._apk_cache = (key, value)
        return value

    def provisioning(self, level: int, label: str, wifi_ssid: str = "", wifi_password: str = "",
                     wifi_security: str = "WPA") -> dict:
        """קוד QR להקמת טלפון מאופס כ"מנהל מכשיר" (Android Enterprise, fully managed).

        הטלפון מוריד את ה-APK מדף ההורדה, בודק את הטביעה, מתקין ומגדיר את FilterPhone כמנהל המכשיר,
        ומקבל קוד הפעלה חד-פעמי (בתוך ה-QR) — כך שהוא מתחבר לסינון לבד, בלי הקלדה."""
        if level not in LEVEL_NAMES or level == 0:
            raise ApiError(400, "רמה לא חוקית")
        apk = Path(self.cfg.apk_dir) / "FilterPhone-universal.apk"
        if not apk.is_file():
            raise ApiError(409, "קובץ האפליקציה עוד לא פורסם בשרת (FilterPhone-universal.apk בדף ההורדה)")
        # הקמת QR מקבלת גם היא את ברירת המחדל ללקוח חדש (רשתות, דפדפן, חנויות חסומות)
        code = self.create_code(security.clean_text(label, 60) or "Device Owner (QR)", level, 1, 2,
                                options=list(DEFAULT_OPTIONS))
        base = f"https://{self.cfg.public_host}"
        extras = {"fp_api": base, "fp_code": code["code"]}
        payload: dict = {
            "android.app.extra.PROVISIONING_DEVICE_ADMIN_COMPONENT_NAME": "com.filterphone.app/.DeviceAdmin",
            "android.app.extra.PROVISIONING_DEVICE_ADMIN_PACKAGE_DOWNLOAD_LOCATION":
                f"{base}/download/FilterPhone-universal.apk",
            "android.app.extra.PROVISIONING_DEVICE_ADMIN_PACKAGE_CHECKSUM": self._apk_checksum(apk),
            "android.app.extra.PROVISIONING_LEAVE_ALL_SYSTEM_APPS_ENABLED": True,
            "android.app.extra.PROVISIONING_ADMIN_EXTRAS_BUNDLE": extras,
        }
        sig = self.cfg.apk_signature_sha256.replace(":", "").strip().lower()
        if len(sig) == 64:
            payload["android.app.extra.PROVISIONING_DEVICE_ADMIN_SIGNATURE_CHECKSUM"] = \
                base64.urlsafe_b64encode(bytes.fromhex(sig)).decode().rstrip("=")
        if wifi_ssid:
            payload["android.app.extra.PROVISIONING_WIFI_SSID"] = security.clean_text(wifi_ssid, 32)
            payload["android.app.extra.PROVISIONING_WIFI_SECURITY_TYPE"] = \
                wifi_security if wifi_security in ("WPA", "WEP", "NONE") else "WPA"
            if wifi_password:
                payload["android.app.extra.PROVISIONING_WIFI_PASSWORD"] = wifi_password[:63]
        import segno

        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        qr = segno.make(text, error="m")
        return {"payload": payload, "qr": qr.svg_data_uri(scale=5, border=4), "code": code,
                "apk_sha256": payload["android.app.extra.PROVISIONING_DEVICE_ADMIN_PACKAGE_CHECKSUM"]}

    # ------------------------------------------------------------ אירועים וסטטיסטיקה

    def device_stats(self, ip: str, day: str | None = None) -> dict[str, int]:
        """הסטטיסטיקה היומית של מכשיר אחד (HMGET — לא כל המכשירים)."""
        day = day or time.strftime("%Y%m%d")
        try:
            vals = self.redis.hmget(f"fp:stats:{day}", [f"{ip}|{k}" for k in STAT_FIELDS])
        except redis.RedisError:
            return {}
        return {k: int(v) for k, v in zip(STAT_FIELDS, vals, strict=True) if v is not None}

    def daily_stats(self, day: str | None = None) -> dict[str, dict[str, int]]:
        day = day or time.strftime("%Y%m%d")
        out: dict[str, dict[str, int]] = {}
        try:
            for fld, n in self.redis.hgetall(f"fp:stats:{day}").items():
                ip, _, action = fld.partition("|")
                out.setdefault(ip, {})[action] = int(n)
        except redis.RedisError:
            pass
        return out

    def stats_range(self, days: int = 7, ip: str | None = None) -> list[dict]:
        """לכל יום: סכום לכל סוג (או למכשיר אחד)."""
        out = []
        for i in range(max(1, min(days, 40)) - 1, -1, -1):
            day = time.strftime("%Y%m%d", time.localtime(time.time() - i * 86400))
            per_ip = self.daily_stats(day)
            rows = [per_ip.get(ip, {})] if ip else list(per_ip.values())
            out.append({"day": day, **{k: sum(r.get(k, 0) for r in rows) for k in STAT_FIELDS}})
        return out

    def recent_events(self, limit: int = 100, ip: str | None = None, kind: str | None = None,
                      action: str | None = None, q: str | None = None) -> list[dict]:
        try:
            entries = self.redis.xrevrange(EVENTS_KEY, count=3000 if (ip or kind or action or q) else
                                           max(1, min(limit, 500)))
        except redis.RedisError:
            return []
        with self.db() as c:
            names = {r["vpn_ip"]: (r["id"], r["name"]) for r in
                     c.execute("SELECT id, vpn_ip, name FROM devices WHERE vpn_ip IS NOT NULL")}
        out = []
        q = (q or "").lower()
        for _, f in entries:
            if ip and f.get("ip") != ip or kind and f.get("kind", "image") != kind \
                    or action and f.get("action") != action:
                continue
            if q and q not in f.get("host", "").lower() and q not in f.get("path", "").lower():
                continue
            dev = names.get(f.get("ip", ""))
            out.append({**f, "kind": f.get("kind", "image"), "device": dev[1] if dev else f.get("ip", ""),
                        "device_id": dev[0] if dev else None})
            if len(out) >= max(1, min(limit, 500)):
                break
        return out

    def overview(self) -> dict:
        devices = self.list_devices()
        totals: dict[str, int] = {}
        for d in devices:
            for k, v in d["today"].items():
                totals[k] = totals.get(k, 0) + v
        t = db.now()
        with self.db() as c:
            pending = c.execute("SELECT kind, count(*) AS n FROM requests WHERE status = 'pending'"
                                " GROUP BY kind").fetchall()
            expiring = c.execute("SELECT count(*) FROM customers WHERE payment = 'paid' AND paid_until IS NOT NULL"
                                 " AND paid_until < ?", (t + 7 * 86400,)).fetchone()[0]
        assert self.adguard is not None
        states: dict[str, int] = {}
        for d in devices:
            st = d["compliance"]["state"]
            states[st] = states.get(st, 0) + 1
        return {
            "devices": len(devices), "online": sum(d["online"] for d in devices),
            "active": sum(d["status"] == "active" for d in devices),
            "compliance": states,
            "pending": {r["kind"]: r["n"] for r in pending}, "expiring": expiring,
            "today": totals, "week": self.stats_range(7),
            "levels": LEVEL_NAMES, "adguard": self.adguard.state(),
            "engine": os.path.exists(self.cfg.engine_socket),
        }

    # ------------------------------------------------------------ בדיקת תמונה

    def test_image(self, data: bytes, level: int) -> dict:
        """שולח תמונה למנוע (explain): ציונים, החלטה בכל רמה, ומה שהטלפון היה מקבל."""
        if level not in (1, 2, 3):
            raise ApiError(400, "רמה לא חוקית")
        if not data:
            raise ApiError(400, "לא התקבלה תמונה")
        try:
            header, body = protocol.call_sync(self.cfg.engine_socket, {"op": "explain", "level": level}, data,
                                              timeout=70)
        except (OSError, protocol.ProtocolError) as e:
            log.error("engine explain failed: %s", e)
            raise ApiError(503, "מנוע הסינון לא זמין כרגע") from e
        if not header.get("ok"):
            log.error("engine explain error: %s", header.get("error"))
            raise ApiError(500, "המנוע לא הצליח לבדוק את התמונה")
        ct = header.get("ct") or ""
        result = None
        if header.get("action") in ("block", "replace") and body:
            result = f"data:{ct or 'image/png'};base64,{base64.b64encode(body).decode()}"
        return {"info": header["info"], "action": header.get("action"), "result_image": result,
                "bytes": len(data)}

    # ------------------------------------------------------------ בריאות ומדדים

    def readiness(self) -> dict[str, bool]:
        """בדיקת מוכנות: כל רכיב בנפרד. db ו-redis חיוניים ל-API; השאר — לניטור."""
        checks: dict[str, bool] = {}
        try:
            with self.db() as c:
                c.execute("SELECT 1").fetchone()
            checks["db"] = True
        except Exception:
            checks["db"] = False
        try:
            checks["redis"] = bool(self.redis.ping())
        except redis.RedisError:
            checks["redis"] = False
        engine = self.engine_metrics(max_age_s=0)
        checks["engine"] = bool(engine and engine.get("healthy"))
        try:
            self.wg.public_key()
            checks["wireguard"] = True
        except WireGuardError:
            checks["wireguard"] = False
        assert self.adguard is not None
        checks["adguard"] = not self.adguard.enabled or not self.adguard.last_error
        return checks

    _engine_cache: tuple[float, dict | None] = (0.0, None)

    def engine_metrics(self, max_age_s: float = 5.0) -> dict | None:
        """מדדי המנוע (דרך ה-socket שלו, עד 1.5 שניות). None = המנוע לא עונה."""
        at, cached = self._engine_cache
        if max_age_s and time.monotonic() - at < max_age_s:
            return cached
        try:
            header, _ = protocol.call_sync(self.cfg.engine_socket, {"op": "metrics"}, b"", timeout=1.5)
            result = header if header.get("ok") else None
        except (OSError, protocol.ProtocolError, ValueError):
            result = None
        self._engine_cache = (time.monotonic(), result)
        return result

    def device_counts(self) -> dict[str, int]:
        with self.db() as c:
            rows = c.execute("SELECT status, count(*) AS n FROM devices GROUP BY status").fetchall()
        out = {r["status"]: r["n"] for r in rows}
        try:
            peers = self.wg.peers()
        except WireGuardError:
            peers = {}
        t = db.now()
        out["online"] = sum(1 for p in peers.values() if p.latest_handshake and t - p.latest_handshake < ONLINE_WINDOW_S)
        return out

    _fleet_cache: tuple[float, dict | None] = (0.0, None)

    def fleet_summary(self, max_age_s: float = 30.0) -> dict:
        """מצב כל המכשירים (תאימות, תקלות ב-24 שעות) — למדדים ולממשק. מחושב לכל היותר פעם ב-30 שניות."""
        at, cached = self._fleet_cache
        if cached is not None and time.monotonic() - at < max_age_s:
            return cached
        try:
            peers = self.wg.peers()
        except WireGuardError:
            peers = {}
        t = db.now()
        states: dict[str, int] = {}
        issues: dict[str, int] = {}
        with self.db() as c:
            rows = c.execute("SELECT * FROM devices").fetchall()
            events = c.execute("SELECT kind, count(*) AS n FROM device_log WHERE ts > ? GROUP BY kind",
                               (t - 86400,)).fetchall()
        for r in rows:
            p = peers.get(r["public_key"])
            comp = self.compliance_of(r, p.latest_handshake if p else 0, t)
            states[comp.state] = states.get(comp.state, 0) + 1
            for i in comp.issues:
                issues[i["code"]] = issues.get(i["code"], 0) + 1
        summary_ = {"states": states, "issues": issues, "events_24h": {e["kind"]: e["n"] for e in events},
                    "total": len(rows)}
        self._fleet_cache = (time.monotonic(), summary_)
        return summary_

    def device_metrics_text(self) -> str:
        counts = self.device_counts()
        fleet = self.fleet_summary()
        lines = ["# HELP fp_devices מכשירים לפי סטטוס", "# TYPE fp_devices gauge"]
        lines += [f'fp_devices{{status="{k}"}} {v}' for k, v in sorted(counts.items())]
        lines += ["# HELP fp_devices_compliance מכשירים לפי מצב תאימות (green/yellow/red/inactive)",
                  "# TYPE fp_devices_compliance gauge"]
        lines += [f'fp_devices_compliance{{state="{k}"}} {v}' for k, v in sorted(fleet["states"].items())]
        lines += ["# HELP fp_device_issues מכשירים עם כל סוג תקלה", "# TYPE fp_device_issues gauge"]
        lines += [f'fp_device_issues{{issue="{k}"}} {v}' for k, v in sorted(fleet["issues"].items())]
        lines += ["# HELP fp_device_events_24h אירועי מכשירים ב-24 השעות האחרונות (כיבוי VPN / מגן...)",
                  "# TYPE fp_device_events_24h gauge"]
        lines += [f'fp_device_events_24h{{kind="{k}"}} {v}' for k, v in sorted(fleet["events_24h"].items())]
        return "\n".join(lines) + "\n"

    def system_status(self) -> dict:
        engine = self.engine_metrics()
        proxy = self.proxy_metrics()
        assert self.adguard is not None
        return {
            "checks": self.readiness(),
            "devices": self.device_counts(),
            "fleet": self.fleet_summary(),
            "engine": engine["metrics"] if engine else None,
            "proxy": proxy,
            "api": observe_snapshot(),
            "adguard": self.adguard.state(),
            "server_time": db.now(),
        }

    def proxy_metrics(self) -> dict[str, float] | None:
        try:
            raw = self.redis.hgetall("fp:metrics:proxy")
        except redis.RedisError:
            return None
        out = {}
        for k, v in raw.items():
            try:
                out[k] = float(v)
            except ValueError:
                continue
        return out or None

    # ------------------------------------------------------------ מנהלים

    def admin_login(self, username: str, password: str) -> dict:
        with self.db() as c:
            row = c.execute("SELECT * FROM admins WHERE username = ?", (username.strip(),)).fetchone()
            if row is None or not security.verify_password(row["password_hash"], password):
                raise ApiError(401, "שם משתמש או סיסמה שגויים")
            token = security.new_token()
            expires = db.now() + 12 * 3600
            c.execute("DELETE FROM admin_sessions WHERE expires_at < ?", (db.now(),))
            c.execute("INSERT INTO admin_sessions (token_hash, admin_id, expires_at) VALUES (?,?,?)",
                      (security.token_hash(token), row["id"], expires))
        return {"token": token, "expires_at": expires, "username": row["username"]}

    def admin_by_token(self, token: str) -> sqlite3.Row:
        with self.db() as c:
            row = c.execute(
                "SELECT a.* FROM admin_sessions s JOIN admins a ON a.id = s.admin_id"
                " WHERE s.token_hash = ? AND s.expires_at > ?", (security.token_hash(token), db.now())).fetchone()
        if row is None:
            raise ApiError(401, "נדרשת התחברות")
        return row

    def admin_logout(self, token: str) -> None:
        with self.db() as c:
            c.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (security.token_hash(token),))

    def set_admin(self, username: str, password: str) -> None:
        if len(password) < 10:
            raise ApiError(400, "סיסמה קצרה מדי (לפחות 10 תווים)")
        with self.db() as c:
            c.execute("INSERT INTO admins (username, password_hash, created_at) VALUES (?,?,?)"
                      " ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash",
                      (username, security.hash_password(password), db.now()))
            c.execute("DELETE FROM admin_sessions WHERE admin_id = (SELECT id FROM admins WHERE username = ?)",
                      (username,))
