"""הלוגיקה העסקית של ה-API: הרשמת מכשירים, קודי הפעלה, רמות, סנכרון.

נפרד מ-FastAPI כדי שאפשר יהיה לבדוק אותו ישירות ולהשתמש בו מכלי שורת פקודה.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import redis

from ..proxy.levels import LEVELS_KEY
from ..settings import Settings
from . import db, security
from .wg import WireGuard, WireGuardError

log = logging.getLogger("filterphone.api")

LEVEL_NAMES = {0: "כבוי", 1: "מחמיר", 2: "בינוני", 3: "בסיסי"}
ONLINE_WINDOW_S = 180           # WireGuard מחדש handshake כל 2 דקות בערך


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Services:
    cfg: Settings
    wg: WireGuard
    redis: redis.Redis

    def conn(self) -> sqlite3.Connection:
        return db.connect(self.cfg.db_path)

    # ------------------------------------------------------------ תעודה

    def ca_pem(self) -> str:
        return Path(self.cfg.mitm_ca_cert).read_text(encoding="ascii")

    def ca_fingerprint(self) -> str:
        import base64
        pem = self.ca_pem()
        body = "".join(line for line in pem.splitlines() if line and not line.startswith("-----"))
        der = base64.b64decode(body)
        return hashlib.sha256(der).hexdigest()

    # ------------------------------------------------------------ Redis

    def push_level(self, ip: str, level: int | None) -> None:
        try:
            if level is None:
                self.redis.hdel(LEVELS_KEY, ip)
            else:
                self.redis.hset(LEVELS_KEY, ip, level)
        except redis.RedisError as e:
            log.error("redis level sync failed for %s: %s", ip, e)

    def _active_devices(self) -> list[sqlite3.Row]:
        with db.closing(self.conn()) as c:
            return c.execute("SELECT public_key, preshared_key, vpn_ip, level FROM devices"
                             " WHERE status = 'active'").fetchall()

    def sync_wireguard(self) -> tuple[int, int]:
        """מיישר את ממשק WireGuard לפי מסד הנתונים. מחזיר (נוספו, הוסרו)."""
        active = self._active_devices()
        return self.wg.reconcile({r["public_key"]: (r["preshared_key"], r["vpn_ip"]) for r in active})

    def sync_redis(self) -> None:
        """כותב מחדש את טבלת הרמות שהפרוקסי קורא."""
        active = self._active_devices()
        pipe = self.redis.pipeline()
        pipe.delete(LEVELS_KEY)
        if active:
            pipe.hset(LEVELS_KEY, mapping={r["vpn_ip"]: r["level"] for r in active})
        pipe.execute()

    def sync_all(self) -> None:
        """מיישר את WireGuard ואת Redis לפי מסד הנתונים. כל אחד בנפרד — כשל באחד לא עוצר את השני."""
        errors = []
        for step in (self.sync_wireguard, self.sync_redis):
            try:
                step()
            except (WireGuardError, redis.RedisError) as e:
                errors.append(f"{step.__name__}: {e}")
        if errors:
            raise RuntimeError("; ".join(errors))

    # ------------------------------------------------------------ הרשמה

    def create_access_request(self, name: str, email: str, phone: str, level: int) -> dict:
        if level not in (1, 2, 3):
            raise ApiError(400, "רמת סינון לא חוקית")
        name = " ".join(name.split())
        email = email.strip().lower()
        phone = phone.strip()
        if not name or len(name) > 100:
            raise ApiError(400, "יש להזין שם תקין")
        if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ApiError(400, "יש להזין כתובת דוא״ל תקינה")
        digits = re.sub(r"\D", "", phone)
        if len(phone) > 30 or not 7 <= len(digits) <= 15:
            raise ApiError(400, "יש להזין מספר טלפון תקין")
        reference = "FP-" + security.new_token()[:8].upper()
        timestamp = db.now()
        with db.closing(self.conn()) as c, db.transaction(c):
            duplicate = c.execute(
                "SELECT 1 FROM access_requests WHERE status = 'pending' AND email = ? AND phone = ?",
                (email, phone),
            ).fetchone()
            if duplicate:
                raise ApiError(409, "כבר קיימת בקשה ממתינה עם פרטים אלה")
            cur = c.execute(
                "INSERT INTO access_requests (reference, name, email, phone, level, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (reference, name, email, phone, level, timestamp, timestamp),
            )
        log.info("access request %s created for level %s", cur.lastrowid, level)
        return {"reference": reference, "status": "pending", "level": level,
                "level_name": LEVEL_NAMES[level], "created_at": timestamp}

    def list_access_requests(self) -> list[dict]:
        with db.closing(self.conn()) as c:
            rows = c.execute(
                "SELECT * FROM access_requests"
                " ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC"
            ).fetchall()
        return [{**dict(r), "level_name": LEVEL_NAMES.get(r["level"], "לא ידוע")} for r in rows]

    def delete_access_request(self, request_id: int) -> None:
        with db.closing(self.conn()) as c:
            if c.execute("DELETE FROM access_requests WHERE id = ?", (request_id,)).rowcount == 0:
                raise ApiError(404, "בקשת ההצטרפות לא נמצאה")

    def decide_access_request(self, request_id: int, approved: bool) -> dict:
        """אישור יוצר קוד חד-פעמי באותה טרנזקציה; הקוד המלא מוחזר פעם אחת בלבד."""
        with db.closing(self.conn()) as c, db.transaction(c):
            row = c.execute("SELECT * FROM access_requests WHERE id = ?", (request_id,)).fetchone()
            if row is None:
                raise ApiError(404, "בקשת ההצטרפות לא נמצאה")
            if row["status"] != "pending":
                raise ApiError(409, "הבקשה כבר טופלה")
            timestamp = db.now()
            if not approved:
                c.execute("UPDATE access_requests SET status='rejected', updated_at=? WHERE id=?",
                          (timestamp, request_id))
                return {"id": request_id, "status": "rejected"}

            code = security.new_code()
            expires = timestamp + 30 * 86400
            label = row["name"][:80]
            cur = c.execute(
                "INSERT INTO codes (code_hash, hint, label, level, uses_left, expires_at, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (security.code_hash(code), code[-3:], label, row["level"], 1, expires, timestamp),
            )
            c.execute("UPDATE access_requests SET status='approved', updated_at=?, code_id=? WHERE id=?",
                      (timestamp, cur.lastrowid, request_id))
        return {"id": request_id, "status": "approved", "code": code, "label": label,
                "level": row["level"], "level_name": LEVEL_NAMES[row["level"]],
                "uses_left": 1, "expires_at": expires}

    def _allocate_ip(self, c: sqlite3.Connection) -> str:
        net = ipaddress.ip_network(self.cfg.vpn_network)
        used = {r["vpn_ip"] for r in c.execute("SELECT vpn_ip FROM devices")}
        used.add(self.cfg.vpn_server_ip)
        for host in net.hosts():
            if int(host) - int(net.network_address) < self.cfg.vpn_first_client:
                continue
            if str(host) not in used:
                return str(host)
        raise ApiError(503, "אין כתובות פנויות ב-VPN")

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
            cur = c.execute(
                "INSERT INTO devices (name, label, platform, app_version, public_key, preshared_key, vpn_ip,"
                " level, token_hash, code_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (name, row["label"], platform[:20], app_version[:20], public_key, psk, ip, row["level"],
                 security.token_hash(token), row["id"], db.now()))
            c.execute("UPDATE codes SET uses_left = uses_left - 1 WHERE id = ?", (row["id"],))
            device_id, level = cur.lastrowid, row["level"]
            # אם WireGuard נכשל — הטרנזקציה מתבטלת, הקוד לא נשרף
            self.wg.add_peer(public_key, psk, ip)
        self.push_level(ip, level)
        log.info("enrolled device %s (%s) at %s, level %s", device_id, name, ip, level)
        return {
            "device_id": device_id,
            "token": token,
            "level": level,
            "level_name": LEVEL_NAMES.get(level, str(level)),
            "vpn": self.vpn_config(ip, psk),
            "ca": {"pem": self.ca_pem(), "sha256": self.ca_fingerprint(), "name": "FilterPhone CA"},
        }

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

    # ------------------------------------------------------------ מכשיר

    def device_by_token(self, token: str) -> sqlite3.Row:
        with db.closing(self.conn()) as c:
            row = c.execute("SELECT * FROM devices WHERE token_hash = ?",
                            (security.token_hash(token),)).fetchone()
            if row is None:
                raise ApiError(401, "המכשיר לא מזוהה")
            c.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (db.now(), row["id"]))
            return row

    def device_status(self, row: sqlite3.Row, client_ip: str = "") -> dict:
        today = self.daily_stats().get(row["vpn_ip"], {})
        return {
            "device_id": row["id"], "name": row["name"], "status": row["status"],
            "level": row["level"], "level_name": LEVEL_NAMES.get(row["level"], ""),
            "vpn_ip": row["vpn_ip"], "server_time": db.now(),
            # הבקשה הגיעה מכתובת ה-VPN של המכשיר = התעבורה באמת עוברת במנהרה ובסינון
            "via_vpn": client_ip == row["vpn_ip"],
            "today": {"blocked": today.get("block", 0), "painted": today.get("replace", 0)},
        }

    def device_vpn(self, row: sqlite3.Row) -> dict:
        return self.vpn_config(row["vpn_ip"], row["preshared_key"])

    def daily_stats(self, day: str | None = None) -> dict[str, dict[str, int]]:
        day = day or time.strftime("%Y%m%d")
        out: dict[str, dict[str, int]] = {}
        try:
            for field, n in self.redis.hgetall(f"fp:stats:{day}").items():
                ip, _, action = field.partition("|")
                out.setdefault(ip, {})[action] = int(n)
        except redis.RedisError:
            pass
        return out

    # ------------------------------------------------------------ ניהול

    def list_devices(self) -> list[dict]:
        try:
            peers = self.wg.peers()
        except WireGuardError:
            peers = {}
        stats = self.daily_stats()
        t = db.now()
        with db.closing(self.conn()) as c:
            rows = c.execute("SELECT * FROM devices ORDER BY id DESC").fetchall()
        out = []
        for r in rows:
            p = peers.get(r["public_key"])
            hs = p.latest_handshake if p else 0
            out.append({
                "id": r["id"], "name": r["name"], "label": r["label"], "platform": r["platform"],
                "app_version": r["app_version"], "vpn_ip": r["vpn_ip"], "level": r["level"],
                "status": r["status"], "legacy": bool(r["legacy"]), "created_at": r["created_at"],
                "last_seen_at": r["last_seen_at"], "last_handshake": hs or None,
                "online": bool(hs and t - hs < ONLINE_WINDOW_S),
                "rx": p.rx if p else 0, "tx": p.tx if p else 0,
                "today": stats.get(r["vpn_ip"], {}),
            })
        return out

    def update_device(self, device_id: int, changes: dict) -> dict:
        allowed = {k: v for k, v in changes.items() if k in {"name", "label", "level", "status"} and v is not None}
        if "level" in allowed and allowed["level"] not in LEVEL_NAMES:
            raise ApiError(400, "רמה לא חוקית")
        if "status" in allowed and allowed["status"] not in ("active", "disabled"):
            raise ApiError(400, "סטטוס לא חוקי")
        with db.closing(self.conn()) as c, db.transaction(c):
            row = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if row is None:
                raise ApiError(404, "מכשיר לא נמצא")
            if allowed:
                sets = ", ".join(f"{k} = ?" for k in allowed)
                c.execute(f"UPDATE devices SET {sets} WHERE id = ?", (*allowed.values(), device_id))
            new = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if new["status"] != row["status"]:
                if new["status"] == "active":
                    self.wg.add_peer(new["public_key"], new["preshared_key"], new["vpn_ip"])
                else:
                    self.wg.remove_peer(new["public_key"])
        self.push_level(new["vpn_ip"], new["level"] if new["status"] == "active" else None)
        return dict(new)

    def delete_device(self, device_id: int) -> None:
        with db.closing(self.conn()) as c, db.transaction(c):
            row = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if row is None:
                raise ApiError(404, "מכשיר לא נמצא")
            c.execute("DELETE FROM devices WHERE id = ?", (device_id,))
            try:
                self.wg.remove_peer(row["public_key"])
            except WireGuardError as e:
                log.warning("remove peer failed (continuing): %s", e)
        self.push_level(row["vpn_ip"], None)
        self.clear_passthrough(row["vpn_ip"])

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

    def recent_events(self, limit: int = 100) -> list[dict]:
        try:
            entries = self.redis.xrevrange("fp:events", count=max(1, min(limit, 500)))
        except redis.RedisError:
            return []
        with db.closing(self.conn()) as c:
            names = {r["vpn_ip"]: r["name"] for r in c.execute("SELECT vpn_ip, name FROM devices")}
        return [{**fields, "device": names.get(fields.get("ip", ""), fields.get("ip", ""))}
                for _, fields in entries]

    def clear_passthrough(self, ip: str) -> int:
        try:
            fields = [f for f in self.redis.hkeys("fp:pt") if f.startswith(ip + "|")]
            if fields:
                self.redis.hdel("fp:pt", *fields)
            return len(fields)
        except redis.RedisError:
            return 0

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
