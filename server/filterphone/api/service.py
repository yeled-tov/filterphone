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
import hashlib
import ipaddress
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import redis

from .. import protocol
from ..proxy.policy import LEVELS_KEY, POLICY_KEY
from ..settings import Settings
from . import db, security
from .adguard import AdGuard, build_rules
from .catalog import CATALOG, LEVELS, Compiled, DeviceSettings, compile_settings, summary
from .wg import WireGuard, WireGuardError

log = logging.getLogger("filterphone.api")

LEVEL_NAMES = {k: v["name"] for k, v in LEVELS.items()}
ONLINE_WINDOW_S = 180           # WireGuard מחדש handshake כל 2 דקות בערך
EVENTS_KEY = "fp:events"
PASSTHROUGH_KEY = "fp:pt"
STAT_FIELDS = ("scanned", "block", "replace", "site", "video")
MAX_REPORTED_APPS = 800

STATUS_MESSAGES = {
    "pending": "הבקשה התקבלה ומחכה לאישור. נעדכן ברגע שהחיבור ייפתח.",
    "rejected": "הבקשה לא אושרה.",
    "disabled": "המכשיר הושבת על ידי המנהל.",
}


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

    def __post_init__(self) -> None:
        if self.adguard is None:
            self.adguard = AdGuard(self.cfg.adguard_url, self.cfg.adguard_user, self.cfg.adguard_password)

    def conn(self) -> sqlite3.Connection:
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
        with db.closing(self.conn()) as c2:
            c2.execute(sql, args)

    def list_audit(self, limit: int = 100) -> list[dict]:
        with db.closing(self.conn()) as c:
            rows = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))
            return [dict(r) for r in rows]

    # ------------------------------------------------------------ מדיניות → Redis / AdGuard

    @staticmethod
    def compiled(row: sqlite3.Row) -> Compiled:
        return compile_settings(row["level"], DeviceSettings.load(row["policy"]))

    def push_device(self, row: sqlite3.Row) -> None:
        """מעדכן את הפרוקסי (Redis) לגבי מכשיר אחד, ומסמן שצריך לסנכרן את ה-DNS."""
        ip = row["vpn_ip"]
        if not ip:
            return
        try:
            if row["status"] == "active":
                pipe = self.redis.pipeline()
                pipe.hset(LEVELS_KEY, ip, row["level"])
                pipe.hset(POLICY_KEY, ip, self.compiled(row).proxy_json(row["level"]))
                pipe.execute()
            else:
                self.redis.hdel(LEVELS_KEY, ip)
                self.redis.hdel(POLICY_KEY, ip)
        except redis.RedisError as e:
            log.error("redis policy sync failed for %s: %s", ip, e)
        self.dirty.set()

    def _active_devices(self) -> list[sqlite3.Row]:
        with db.closing(self.conn()) as c:
            return c.execute("SELECT * FROM devices WHERE status = 'active' AND vpn_ip IS NOT NULL").fetchall()

    def sync_wireguard(self) -> tuple[int, int]:
        """מיישר את ממשק WireGuard לפי מסד הנתונים. מחזיר (נוספו, הוסרו)."""
        active = self._active_devices()
        return self.wg.reconcile({r["public_key"]: (r["preshared_key"], r["vpn_ip"]) for r in active})

    def sync_redis(self) -> None:
        """כותב מחדש את טבלאות המדיניות שהפרוקסי קורא."""
        active = self._active_devices()
        pipe = self.redis.pipeline()
        pipe.delete(LEVELS_KEY, POLICY_KEY)
        if active:
            pipe.hset(LEVELS_KEY, mapping={r["vpn_ip"]: r["level"] for r in active})
            pipe.hset(POLICY_KEY, mapping={r["vpn_ip"]: self.compiled(r).proxy_json(r["level"]) for r in active})
        pipe.execute()

    def adguard_rules(self) -> list[str]:
        return build_rules((r["vpn_ip"], self.compiled(r).hosts) for r in self._active_devices())

    def sync_adguard(self, force: bool = False) -> bool:
        assert self.adguard is not None
        return self.adguard.sync(self.adguard_rules(), force=force)

    def sync_all(self) -> None:
        """מיישר WireGuard, Redis ו-AdGuard לפי מסד הנתונים. כל אחד בנפרד — כשל באחד לא עוצר אחר."""
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
        return {
            "address": f"{ip}/32",
            "dns": [self.cfg.vpn_server_ip],
            "mtu": 1280,
            "server_public_key": self.wg.public_key(),
            "preshared_key": psk,
            "endpoint": self.cfg.vpn_endpoint,
            "allowed_ips": ["0.0.0.0/0", "::/0"],
            "persistent_keepalive": 25,
        }

    # ------------------------------------------------------------ הפעלה בקוד

    def enroll(self, code: str, public_key: str, name: str, platform: str, app_version: str) -> dict:
        if not security.is_wg_key(public_key):
            raise ApiError(400, "מפתח ציבורי לא תקין")
        name = (name or "").strip()[:60] or "מכשיר"
        token = security.new_token()
        psk = security.new_preshared_key()
        with db.closing(self.conn()) as c, db.transaction(c):
            row = c.execute("SELECT * FROM codes WHERE code_hash = ?", (security.code_hash(code),)).fetchone()
            if row is None or row["uses_left"] <= 0 or (row["expires_at"] and row["expires_at"] < db.now()):
                raise ApiError(403, "קוד ההפעלה לא תקין או שפג תוקפו")
            if c.execute("SELECT 1 FROM devices WHERE public_key = ?", (public_key,)).fetchone():
                raise ApiError(409, "המכשיר כבר רשום")
            ip = self._allocate_ip(c)
            t = db.now()
            cur = c.execute(
                "INSERT INTO devices (name, label, platform, app_version, public_key, preshared_key, vpn_ip,"
                " level, token_hash, code_id, created_at, approved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, row["label"], platform[:20], app_version[:20], public_key, psk, ip, row["level"],
                 security.token_hash(token), row["id"], t, t))
            c.execute("UPDATE codes SET uses_left = uses_left - 1 WHERE id = ?", (row["id"],))
            device_id, level = cur.lastrowid, row["level"]
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

    # ------------------------------------------------------------ הרשמה מהאפליקציה

    def register(self, *, name: str, phone: str, email: str, level: int, options: list[str], note: str,
                 public_key: str, device_name: str, platform: str, app_version: str) -> dict:
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
        with db.closing(self.conn()) as c, db.transaction(c):
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
                " customer_id, policy, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (dev_name, name, platform[:20], app_version[:20], public_key, level, "pending",
                 security.token_hash(token), customer_id, DeviceSettings(options=opts).dump(), t)).lastrowid
            request_id = c.execute(
                "INSERT INTO requests (kind, device_id, customer_id, level, options, note, created_at)"
                " VALUES ('join',?,?,?,?,?,?)",
                (device_id, customer_id, level, json.dumps(opts), note, t)).lastrowid
            self.audit(f"device:{device_id}", "register", f"request:{request_id}", f"{name} {phone_n}", c)
        log.info("registration request %s from %s (device %s)", request_id, phone_n, device_id)
        return {"device_id": device_id, "token": token, "status": "pending", "request_id": request_id,
                "message": STATUS_MESSAGES["pending"]}

    # ------------------------------------------------------------ מכשיר (מהאפליקציה)

    def device_by_token(self, token: str) -> sqlite3.Row:
        with db.closing(self.conn()) as c:
            row = c.execute("SELECT * FROM devices WHERE token_hash = ?",
                            (security.token_hash(token),)).fetchone()
            if row is None:
                raise ApiError(401, "המכשיר לא מזוהה")
            c.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (db.now(), row["id"]))
            return row

    def device_status(self, row: sqlite3.Row, client_ip: str = "") -> dict:
        out: dict = {
            "device_id": row["id"], "name": row["name"], "status": row["status"],
            "level": row["level"], "level_name": LEVEL_NAMES.get(row["level"], ""),
            "server_time": db.now(), "message": STATUS_MESSAGES.get(row["status"], ""),
        }
        with db.closing(self.conn()) as c:
            req = c.execute("SELECT id, kind, status, admin_note, created_at, resolved_at FROM requests"
                            " WHERE device_id = ? ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
        if req is not None:
            out["request"] = {"id": req["id"], "kind": req["kind"], "status": req["status"],
                              "created_at": req["created_at"], "resolved_at": req["resolved_at"]}
            if row["status"] == "rejected" and req["admin_note"]:
                out["message"] = f"{STATUS_MESSAGES['rejected']} {req['admin_note']}"
        if row["status"] != "active":
            return out
        settings = DeviceSettings.load(row["policy"])
        comp = compile_settings(row["level"], settings)
        today = self.daily_stats().get(row["vpn_ip"], {})
        out.update({
            "vpn_ip": row["vpn_ip"],
            # הבקשה הגיעה מכתובת ה-VPN של המכשיר = התעבורה באמת עוברת במנהרה ובסינון
            "via_vpn": client_ip == row["vpn_ip"],
            "today": {"blocked": today.get("block", 0), "painted": today.get("replace", 0),
                      "sites": today.get("site", 0)},
            "blocks": summary(settings),
            "device_policy": comp.device_json(),
            "apps_report_needed": not row["apps_at"] or db.now() - row["apps_at"] > 86400,
        })
        return out

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
        with db.closing(self.conn()) as c, db.transaction(c):
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
        with db.closing(self.conn()) as c, db.transaction(c):
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
            with db.closing(self.conn()) as c:
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
        with db.closing(self.conn()) as c:
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
        with db.closing(self.conn()) as c, db.transaction(c):
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
            self.audit(admin, "approve", f"request:{request_id}",
                       f"{req['kind']} device:{dev['id']} level:{level} options:{','.join(settings.options)}", c)
            new = c.execute("SELECT * FROM devices WHERE id = ?", (dev["id"],)).fetchone()
            if req["kind"] == "join":
                # אם WireGuard נכשל — הכול מתבטל, והבקשה נשארת ממתינה
                self.wg.add_peer(new["public_key"], new["preshared_key"], new["vpn_ip"])
        self.push_device(new)
        log.info("request %s approved by %s (device %s, level %s)", request_id, admin, new["id"], level)
        return {"device_id": new["id"], "status": new["status"], "vpn_ip": new["vpn_ip"]}

    def reject_request(self, request_id: int, admin: str, note: str) -> None:
        with db.closing(self.conn()) as c, db.transaction(c):
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
        with db.closing(self.conn()) as c, db.transaction(c):
            if c.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone() is None:
                raise ApiError(404, "לקוח לא נמצא")
            self._update_customer(c, customer_id, changes)
            self.audit(admin, "customer", f"customer:{customer_id}",
                       ", ".join(k for k, v in changes.items() if v is not None), c)
            return dict(c.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone())

    def list_customers(self) -> list[dict]:
        with db.closing(self.conn()) as c:
            rows = c.execute(
                "SELECT c.*, (SELECT count(*) FROM devices d WHERE d.customer_id = c.id) AS devices,"
                " (SELECT count(*) FROM devices d WHERE d.customer_id = c.id AND d.status = 'active') AS active"
                " FROM customers c ORDER BY c.id DESC").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ מכשירים (מנהל)

    def _device_row_out(self, r: sqlite3.Row, peers: dict, stats: dict, t: int) -> dict:
        p = peers.get(r["public_key"])
        hs = p.latest_handshake if p else 0
        settings = DeviceSettings.load(r["policy"])
        guard = _json_dict(r["guard"])
        return {
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
        with db.closing(self.conn()) as c:
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
        with db.closing(self.conn()) as c:
            r = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        if r is None:
            raise ApiError(404, "מכשיר לא נמצא")
        return r

    def device_detail(self, device_id: int) -> dict:
        with db.closing(self.conn()) as c:
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
        blocked = set(comp.apps)
        out.update({
            "settings": settings.as_dict(),
            "compiled": {"hosts": len(comp.hosts), "apps": len(comp.apps), "images": comp.images,
                         "video": comp.video, "app_images": comp.app_images,
                         "needs_guard": any(CATALOG[o].needs_guard for o in settings.options)},
            "customer": dict(cust) if cust else None,
            "requests": [{**dict(q), "options": _json_list(q["options"])} for q in reqs],
            "apps": [{**a, "blocked": a["package"] in blocked} for a in _json_list(r["apps"])],
            "apps_at": r["apps_at"],
            "history": self.stats_range(7, r["vpn_ip"]) if r["vpn_ip"] else [],
            "events": self.recent_events(50, ip=r["vpn_ip"]) if r["vpn_ip"] else [],
        })
        return out

    def update_device(self, device_id: int, changes: dict, admin: str = "admin") -> dict:
        allowed = {k: v for k, v in changes.items() if k in {"name", "label", "level", "status"} and v is not None}
        if "level" in allowed and allowed["level"] not in LEVEL_NAMES:
            raise ApiError(400, "רמה לא חוקית")
        if "status" in allowed and allowed["status"] not in ("active", "disabled"):
            raise ApiError(400, "סטטוס לא חוקי")
        policy_keys = ("options", "custom_hosts", "allow_hosts", "custom_apps", "app_images")
        policy_changes = {k: changes[k] for k in policy_keys if changes.get(k) is not None}
        with db.closing(self.conn()) as c, db.transaction(c):
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
            if allowed:
                sets = ", ".join(f"{k} = ?" for k in allowed)
                c.execute(f"UPDATE devices SET {sets} WHERE id = ?", (*allowed.values(), device_id))
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

    def delete_device(self, device_id: int, admin: str = "admin") -> None:
        with db.closing(self.conn()) as c, db.transaction(c):
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
            self.redis.hdel(LEVELS_KEY, row["vpn_ip"])
            self.redis.hdel(POLICY_KEY, row["vpn_ip"])
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

    def create_code(self, label: str, level: int, uses: int, expires_days: int | None) -> dict:
        if level not in LEVEL_NAMES:
            raise ApiError(400, "רמה לא חוקית")
        uses = max(1, min(int(uses), 100))
        code = security.new_code()
        expires = db.now() + int(expires_days) * 86400 if expires_days else None
        with db.closing(self.conn()) as c:
            cur = c.execute(
                "INSERT INTO codes (code_hash, hint, label, level, uses_left, expires_at, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (security.code_hash(code), code[-3:], label.strip()[:80], level, uses, expires, db.now()))
        return {"id": cur.lastrowid, "code": code, "label": label, "level": level,
                "uses_left": uses, "expires_at": expires}

    def list_codes(self) -> list[dict]:
        with db.closing(self.conn()) as c:
            rows = c.execute("SELECT id, hint, label, level, uses_left, expires_at, created_at FROM codes"
                             " ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

    def delete_code(self, code_id: int) -> None:
        with db.closing(self.conn()) as c:
            if c.execute("DELETE FROM codes WHERE id = ?", (code_id,)).rowcount == 0:
                raise ApiError(404, "קוד לא נמצא")

    # ------------------------------------------------------------ אירועים וסטטיסטיקה

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
        with db.closing(self.conn()) as c:
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
        with db.closing(self.conn()) as c:
            pending = c.execute("SELECT kind, count(*) AS n FROM requests WHERE status = 'pending'"
                                " GROUP BY kind").fetchall()
            expiring = c.execute("SELECT count(*) FROM customers WHERE payment = 'paid' AND paid_until IS NOT NULL"
                                 " AND paid_until < ?", (t + 7 * 86400,)).fetchone()[0]
        assert self.adguard is not None
        return {
            "devices": len(devices), "online": sum(d["online"] for d in devices),
            "active": sum(d["status"] == "active" for d in devices),
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
                                              timeout=60)
        except (OSError, protocol.ProtocolError) as e:
            raise ApiError(503, f"מנוע הסינון לא זמין: {e}") from e
        if not header.get("ok"):
            raise ApiError(500, header.get("error") or "שגיאה במנוע")
        ct = header.get("ct") or ""
        result = None
        if header.get("action") in ("block", "replace") and body:
            result = f"data:{ct or 'image/png'};base64,{base64.b64encode(body).decode()}"
        return {"info": header["info"], "action": header.get("action"), "result_image": result,
                "bytes": len(data)}

    # ------------------------------------------------------------ מנהלים

    def admin_login(self, username: str, password: str) -> dict:
        with db.closing(self.conn()) as c:
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
        with db.closing(self.conn()) as c:
            row = c.execute(
                "SELECT a.* FROM admin_sessions s JOIN admins a ON a.id = s.admin_id"
                " WHERE s.token_hash = ? AND s.expires_at > ?", (security.token_hash(token), db.now())).fetchone()
        if row is None:
            raise ApiError(401, "נדרשת התחברות")
        return row

    def admin_logout(self, token: str) -> None:
        with db.closing(self.conn()) as c:
            c.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (security.token_hash(token),))

    def set_admin(self, username: str, password: str) -> None:
        if len(password) < 10:
            raise ApiError(400, "סיסמה קצרה מדי (לפחות 10 תווים)")
        with db.closing(self.conn()) as c:
            c.execute("INSERT INTO admins (username, password_hash, created_at) VALUES (?,?,?)"
                      " ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash",
                      (username, security.hash_password(password), db.now()))
            c.execute("DELETE FROM admin_sessions WHERE admin_id = (SELECT id FROM admins WHERE username = ?)",
                      (username,))
