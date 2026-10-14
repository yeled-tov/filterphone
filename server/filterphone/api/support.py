"""תמיכה: שיחה בין הלקוח (באפליקציה) לבין המנהל — בממשק הניהול ובוואטסאפ של המנהל.

* הלקוח כותב באפליקציה, גם אם אין לו וואטסאפ. ההודעה נשמרת כאן, ונשלחת לוואטסאפ של המנהל
  (WhatsApp Cloud API הרשמי, אם הוגדר) יחד עם מצב טכני קצר של הטלפון.
* המנהל עונה ב"השב" (Reply) על ההודעה בוואטסאפ, או כותב "#<מספר מכשיר> טקסט", או עונה מממשק
  הניהול — והתשובה מגיעה לאפליקציה (ומהדיווח התקופתי — גם התראה בטלפון).
* תשובות מוואטסאפ מתקבלות רק עם חתימה תקינה של Meta (X-Hub-Signature-256) ורק מהמספר של המנהל.
* נשמר רק מה שהלקוח כתב בעצמו ומצב טכני (VPN / נגישות / גרסה) — שום תוכן אחר מהטלפון.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass, replace

from . import db, security
from .service import ApiError, Services

log = logging.getLogger("filterphone.support")

MAX_TEXT = 2000
KEEP_DAYS = 365
GRAPH = "https://graph.facebook.com"
WA_TIMEOUT_S = 10
ERR_OUTSIDE_WINDOW = 131047        # עברו 24 שעות מאז שהמנהל כתב למספר העסקי → רק תבנית מאושרת

# מצב טכני שהאפליקציה מצרפת לפנייה — רק השדות והסוגים האלה נשמרים
_DIAG: dict[str, type] = {
    "app_version": str, "sdk": int, "vpn": str, "vpn_error": str, "accessibility": bool, "device_owner": bool,
    "battery_unrestricted": bool, "ca_installed": bool, "policy_version": int, "level": int, "paused": bool,
    "last_sync": int, "topic": str,
}

# הגדרות וואטסאפ: משתנה סביבה FP_WA_* גובר; אחרת מה שהמנהל שמר בממשק (טבלת meta)
_WA_FIELDS = ("token", "phone_number_id", "admin_number", "verify_token", "app_secret", "template",
              "template_lang", "public_number", "graph_version", "bot_token")
_WA_SECRET = {"token", "app_secret", "bot_token"}


class WaError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class WaConfig:
    token: str = ""
    phone_number_id: str = ""
    admin_number: str = ""          # הוואטסאפ של המנהל — לשם נשלחות הפניות, וממנו בלבד מתקבלות תשובות
    verify_token: str = ""          # מחרוזת שהמנהל בוחר ומדביק גם ב-Meta (אימות ה-webhook)
    app_secret: str = ""            # לאימות החתימה של כל הודעה נכנסת
    template: str = ""              # תבנית מאושרת — כשעברו 24 שעות מההודעה האחרונה של המנהל
    template_lang: str = "he"
    public_number: str = ""         # המספר שלקוחות עם וואטסאפ יכולים לכתוב אליו ישירות
    graph_version: str = "v23.0"
    bot_token: str = ""          # טוקן משותף לשירות בוט הוואטסאפ הלא-רשמי (whatsapp-web.js)

    @property
    def can_send(self) -> bool:
        return bool(self.token and self.phone_number_id and self.admin_number)

    @property
    def can_receive(self) -> bool:
        return bool(self.verify_token and self.app_secret and self.admin_number)


# ---------------------------------------------------------------- עזרים טהורים (נבדקים ביחידה)

def wa_number(phone: str | None) -> str:
    """מספר לוואטסאפ: ספרות בלבד בפורמט בינלאומי (0501234567 → 972501234567). ריק אם לא תקין."""
    p = security.normalize_phone(phone)
    if p.startswith("0"):
        return "972" + p[1:]
    return p[1:] if p.startswith("+") else ""


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256: sha256=<hex> — HMAC של הגוף הגולמי עם ה-App Secret."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    want = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header[7:].strip().lower())


@dataclass(frozen=True)
class Incoming:
    sender: str          # מספר השולח (ספרות)
    wa_id: str           # מזהה ההודעה הנכנסת
    text: str | None     # None = לא הודעת טקסט (תמונה, הקלטה...)
    reply_to: str = ""   # ההודעה שעליה ענו ("השב")


def parse_incoming(payload: dict, phone_number_id: str) -> list[Incoming]:
    """הודעות נכנסות מה-webhook של WhatsApp Cloud API (מתעלם מסטטוסים ומחשבונות אחרים)."""
    out: list[Incoming] = []
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return out
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            value = (change or {}).get("value") or {}
            if change.get("field") != "messages" or not isinstance(value, dict):
                continue
            if phone_number_id and str((value.get("metadata") or {}).get("phone_number_id", "")) != phone_number_id:
                continue
            for m in value.get("messages") or []:
                if not isinstance(m, dict) or not m.get("id") or not m.get("from"):
                    continue
                text = (m.get("text") or {}).get("body") if m.get("type") == "text" else None
                out.append(Incoming(re.sub(r"\D", "", str(m["from"])), str(m["id"])[:200],
                                    str(text) if text is not None else None,
                                    str((m.get("context") or {}).get("id", ""))[:200]))
    return out


_TARGET = re.compile(r"^\s*#(\d{1,9})\s*[:\-–]?\s*(.*)$", re.S)


def parse_target(text: str) -> tuple[int | None, str]:
    """'#12 שלום' → (12, 'שלום'). בלי סולמית → (None, הטקסט)."""
    m = _TARGET.match(text or "")
    return (int(m.group(1)), m.group(2).strip()) if m else (None, (text or "").strip())


def clean_diag(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for k, typ in _DIAG.items():
        v = raw.get(k)
        if v is None or (typ is not bool and isinstance(v, bool)):
            continue
        if typ is bool and isinstance(v, bool):
            out[k] = v
        elif typ is int and isinstance(v, int | float):
            out[k] = int(v)
        elif typ is str and isinstance(v, str | int | float):
            out[k] = security.clean_text(str(v), 80)
    return out


def _yes(v) -> str:
    return "✓" if v else "✗"


def diag_line(d: dict) -> str:
    parts = []
    if "vpn" in d:
        parts.append(f"VPN: {d['vpn']}")
    if "accessibility" in d:
        parts.append(f"נגישות {_yes(d['accessibility'])}")
    if "device_owner" in d:
        parts.append(f"מנהל מכשיר {_yes(d['device_owner'])}")
    if d.get("paused"):
        parts.append("בהשהיה")
    if d.get("app_version"):
        parts.append(f"גרסה {d['app_version']}")
    if d.get("vpn_error"):
        parts.append(f"שגיאה: {d['vpn_error']}")
    return " · ".join(parts)


def admin_notice(device_id: int, customer: str, device_name: str, text: str, diag: dict) -> str:
    """ההודעה שנשלחת לוואטסאפ של המנהל."""
    head = f"📩 {customer or 'לקוח'} · מכשיר #{device_id}" + (f" ({device_name})" if device_name else "")
    lines = [head, "", text]
    if d := diag_line(diag):
        lines += ["", d]
    lines += ["", "↩️ כדי לענות: לחצו \"השב\" על ההודעה הזו."]
    return "\n".join(lines)[:4000]


def _template_param(s: str, limit: int) -> str:
    # פרמטר תבנית: בלי ירידות שורה / טאבים / יותר מ-4 רווחים רצופים (דרישת Meta)
    return re.sub(r"\s+", " ", s).strip()[:limit] or "-"


# ---------------------------------------------------------------- לקוח WhatsApp Cloud API

class WaClient:
    def __init__(self, cfg: WaConfig):
        self.cfg = cfg

    def _post(self, payload: dict) -> dict:
        url = f"{GRAPH}/{self.cfg.graph_version}/{self.cfg.phone_number_id}/messages"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
            "Authorization": f"Bearer {self.cfg.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=WA_TIMEOUT_S) as r:
                return json.loads(r.read(200_000) or b"{}")
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read(20_000)).get("error") or {}
            except (ValueError, OSError):
                err = {}
            raise WaError(int(err.get("code") or e.code), str(err.get("message") or e.reason)[:200]) from e
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise WaError(0, f"אין חיבור לוואטסאפ: {e.__class__.__name__}") from e

    @staticmethod
    def _id(res: dict) -> str:
        msgs = res.get("messages") or [{}]
        return str(msgs[0].get("id", ""))

    def text(self, to: str, body: str, reply_to: str = "") -> str:
        payload = {"messaging_product": "whatsapp", "to": to, "type": "text",
                   "text": {"body": body[:4096], "preview_url": False}}
        if reply_to:
            payload["context"] = {"message_id": reply_to}
        return self._id(self._post(payload))

    def template(self, to: str, name: str, lang: str, params: list[str]) -> str:
        return self._id(self._post({
            "messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {"name": name, "language": {"code": lang or "he"},
                         "components": [{"type": "body",
                                         "parameters": [{"type": "text", "text": p} for p in params]}]}}))

    def react(self, to: str, message_id: str, emoji: str = "✅") -> None:
        self._post({"messaging_product": "whatsapp", "to": to, "type": "reaction",
                    "reaction": {"message_id": message_id, "emoji": emoji}})


# ---------------------------------------------------------------- שולחן התמיכה

def _msg_out(r: sqlite3.Row, viewer: str) -> dict:
    out = {"id": r["id"], "from": r["sender"], "text": r["body"], "ts": r["ts"], "via": r["via"]}
    if viewer == "admin":
        out |= {"author": r["author"], "read": bool(r["read_at"]), "wa_error": r["wa_error"],
                "diag": json.loads(r["diag"]) if r["diag"] else None}
    return out


class SupportDesk:
    """הכול דרך המסד של Services. שליחה לוואטסאפ — מחוץ לבקשה (BackgroundTasks), כדי לא לעכב."""

    def __init__(self, svc: Services, client_factory=WaClient):
        self.svc = svc
        self.client_factory = client_factory

    # --- הגדרות ---

    def wa_config(self) -> WaConfig:
        with self.svc.db() as c:
            stored = {r["key"][3:]: r["value"] for r in
                      c.execute("SELECT key, value FROM meta WHERE key LIKE 'wa.%'").fetchall()}
        values = {}
        for f in _WA_FIELDS:
            env = getattr(self.svc.cfg, f"wa_{f}", "")
            v = env or stored.get(f, "")
            if v:
                values[f] = v
        return replace(WaConfig(), **values)

    def wa_status(self) -> dict:
        cfg = self.wa_config()
        with self.svc.db() as c:
            meta = {r["key"]: r["value"] for r in
                    c.execute("SELECT key, value FROM meta WHERE key IN ('wa_last_ok', 'wa_last_error')")}
        shown = {f: getattr(cfg, f) for f in _WA_FIELDS if f not in _WA_SECRET}
        return shown | {
            "token_set": bool(cfg.token), "app_secret_set": bool(cfg.app_secret),
            "bot_token_set": bool(cfg.bot_token),
            "token_hint": f"…{cfg.token[-4:]}" if len(cfg.token) > 8 else "",
            "can_send": cfg.can_send, "can_receive": cfg.can_receive,
            "webhook_url": f"https://{self.svc.cfg.public_host}/v1/whatsapp/webhook",
            "last_ok": int(meta["wa_last_ok"]) if meta.get("wa_last_ok") else None,
            "last_error": meta.get("wa_last_error", ""),
        }

    def save_wa_config(self, admin: str, changes: dict) -> dict:
        """שדה ריק בסוד (טוקן / App Secret) = בלי שינוי; clear_secrets מוחק אותם."""
        rows: dict[str, str | None] = {}
        for f in _WA_FIELDS:
            if f not in changes or changes[f] is None:
                continue
            v = str(changes[f]).strip()
            if f in _WA_SECRET and not v:
                continue
            if f in ("admin_number", "public_number") and v:
                v = wa_number(v)
                if not v:
                    raise ApiError(400, "מספר וואטסאפ לא תקין")
            elif f == "phone_number_id" and v and not v.isdigit():
                raise ApiError(400, "Phone number ID הוא מספר (מתוך WhatsApp Manager)")
            elif f == "graph_version" and v and not re.fullmatch(r"v\d{2}\.\d", v):
                raise ApiError(400, "גרסת Graph לא תקינה (למשל v23.0)")
            elif f == "template" and v and not re.fullmatch(r"[a-z0-9_]{1,512}", v):
                raise ApiError(400, "שם תבנית: אותיות קטנות באנגלית, ספרות וקו תחתון")
            elif len(v) > 1024 or re.search(r"\s", v):
                raise ApiError(400, "ערך לא תקין")
            rows[f] = v or None
        if changes.get("clear_secrets"):
            rows |= {f: None for f in _WA_SECRET}
        with self.svc.db() as c, db.transaction(c):
            for f, v in rows.items():
                if v is None:
                    c.execute("DELETE FROM meta WHERE key = ?", (f"wa.{f}",))
                else:
                    c.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET"
                              " value = excluded.value", (f"wa.{f}", v))
            if rows:
                self.svc.audit(admin, "whatsapp_settings", "", ", ".join(sorted(rows)), c)
        return self.wa_status()

    def _mark(self, ok: bool, error: str = "") -> None:
        with self.svc.db() as c:
            if ok:
                c.execute("INSERT INTO meta (key, value) VALUES ('wa_last_ok', ?) ON CONFLICT(key) DO UPDATE"
                          " SET value = excluded.value", (str(db.now()),))
            c.execute("INSERT INTO meta (key, value) VALUES ('wa_last_error', ?) ON CONFLICT(key) DO UPDATE"
                      " SET value = excluded.value", (error[:200],))

    # --- הטלפון ---

    def device_post(self, row: sqlite3.Row, text: str, diag: dict | None) -> dict:
        body = security.clean_text(text, MAX_TEXT, multiline=True)
        if not body:
            raise ApiError(400, "ההודעה ריקה")
        d = clean_diag(diag)
        t = db.now()
        with self.svc.db() as c, db.transaction(c):
            mid = c.execute("INSERT INTO support_messages (device_id, sender, via, body, diag, ts)"
                            " VALUES (?, 'customer', 'app', ?, ?, ?)",
                            (row["id"], body, json.dumps(d, ensure_ascii=False) if d else None, t)).lastrowid
            self.svc.log_device(c, row["id"], "support_message", body[:60])
        log.info("support message from device %s (%d chars)", row["id"], len(body))
        return {"id": mid, "from": "customer", "text": body, "ts": t, "via": "app",
                "forwarded": self.wa_config().can_send}

    def device_messages(self, row: sqlite3.Row, after: int = 0, limit: int = 200) -> dict:
        with self.svc.db() as c:
            rows = c.execute("SELECT * FROM support_messages WHERE device_id = ? AND id > ? ORDER BY id DESC"
                             " LIMIT ?", (row["id"], max(0, after), limit)).fetchall()
            c.execute("UPDATE support_messages SET read_at = ? WHERE device_id = ? AND sender = 'support'"
                      " AND read_at IS NULL", (db.now(), row["id"]))
        return {"messages": [_msg_out(r, "device") for r in reversed(rows)],
                "whatsapp": self.wa_config().public_number}

    def device_unread(self, device_id: int) -> dict:
        with self.svc.db() as c:
            n, last = c.execute("SELECT COUNT(*), MAX(id) FROM support_messages WHERE device_id = ?"
                                " AND sender = 'support' AND read_at IS NULL", (device_id,)).fetchone()
        return {"unread": n, "last_id": last or 0}

    # --- המנהל ---

    def admin_unread(self) -> int:
        with self.svc.db() as c:
            return c.execute("SELECT COUNT(DISTINCT device_id) FROM support_messages WHERE sender = 'customer'"
                             " AND read_at IS NULL").fetchone()[0]

    def threads(self, limit: int = 200) -> list[dict]:
        with self.svc.db() as c:
            rows = c.execute(
                "SELECT m.device_id, d.name AS device_name, d.status, cu.name AS customer, m.body, m.sender, m.ts,"
                " (SELECT COUNT(*) FROM support_messages u WHERE u.device_id = m.device_id"
                "  AND u.sender = 'customer' AND u.read_at IS NULL) AS unread"
                " FROM support_messages m JOIN devices d ON d.id = m.device_id"
                " LEFT JOIN customers cu ON cu.id = d.customer_id"
                " WHERE m.id = (SELECT MAX(id) FROM support_messages x WHERE x.device_id = m.device_id)"
                " ORDER BY m.id DESC LIMIT ?", (limit,)).fetchall()
        return [{"device_id": r["device_id"], "device_name": r["device_name"], "status": r["status"],
                 "customer": r["customer"] or "", "last_text": r["body"][:140], "last_from": r["sender"],
                 "last_ts": r["ts"], "unread": r["unread"]} for r in rows]

    def thread(self, device_id: int) -> dict:
        row = self.svc.device_row(device_id)
        with self.svc.db() as c:
            msgs = c.execute("SELECT * FROM (SELECT * FROM support_messages WHERE device_id = ? ORDER BY id DESC"
                             " LIMIT 500) ORDER BY id", (device_id,)).fetchall()
            c.execute("UPDATE support_messages SET read_at = ? WHERE device_id = ? AND sender = 'customer'"
                      " AND read_at IS NULL", (db.now(), device_id))
            cust = c.execute("SELECT name FROM customers WHERE id = ?", (row["customer_id"],)).fetchone() \
                if row["customer_id"] else None
        return {"device": {"id": row["id"], "name": row["name"], "status": row["status"],
                           "customer": cust["name"] if cust else ""},
                "messages": [_msg_out(m, "admin") for m in msgs]}

    def reply(self, device_id: int, author: str, text: str, via: str = "dashboard", wa_id: str = "") -> dict | None:
        """תשובה ללקוח. wa_id (מוואטסאפ) — הודעה שכבר נשמרה (Meta שולחת שוב) מחזירה None."""
        body = security.clean_text(text, MAX_TEXT, multiline=True)
        if not body:
            raise ApiError(400, "ההודעה ריקה")
        self.svc.device_row(device_id)                      # 404 אם אין מכשיר כזה
        with self.svc.db() as c, db.transaction(c):
            if wa_id and c.execute("SELECT 1 FROM support_messages WHERE wa_id = ?", (wa_id,)).fetchone():
                return None
            mid = c.execute("INSERT INTO support_messages (device_id, sender, via, author, body, ts, wa_id)"
                            " VALUES (?, 'support', ?, ?, ?, ?, ?)",
                            (device_id, via, author, body, db.now(), wa_id)).lastrowid
            # המנהל ענה = הפנייה טופלה
            c.execute("UPDATE support_messages SET read_at = ? WHERE device_id = ? AND sender = 'customer'"
                      " AND read_at IS NULL", (db.now(), device_id))
            self.svc.audit(author, "support_reply", f"device:{device_id}", via, c)
        return {"id": mid, "from": "support", "text": body, "via": via}

    # --- וואטסאפ ---

    def notify_admin(self, message_id: int) -> None:
        """שולח את הפנייה לוואטסאפ של המנהל (רץ ברקע). כשלון לא מאבד את ההודעה — היא בממשק."""
        cfg = self.wa_config()
        # כשמוגדר בוט לא-רשמי (whatsapp-web.js) — הוא מושך מה-outbox ושולח; לא שולחים גם דרך Cloud API
        if cfg.bot_token or not cfg.can_send:
            return
        with self.svc.db() as c:
            m = c.execute("SELECT m.*, d.name AS device_name, cu.name AS customer FROM support_messages m"
                          " JOIN devices d ON d.id = m.device_id LEFT JOIN customers cu ON cu.id = d.customer_id"
                          " WHERE m.id = ?", (message_id,)).fetchone()
        if m is None:
            return
        diag = json.loads(m["diag"]) if m["diag"] else {}
        client = self.client_factory(cfg)
        wa_id, error = "", ""
        try:
            wa_id = client.text(cfg.admin_number, admin_notice(m["device_id"], m["customer"] or "",
                                                               m["device_name"] or "", m["body"], diag))
        except WaError as e:
            if e.code == ERR_OUTSIDE_WINDOW and cfg.template:
                try:
                    wa_id = client.template(cfg.admin_number, cfg.template, cfg.template_lang, [
                        _template_param(f"{m['customer'] or 'לקוח'} (#{m['device_id']})", 60),
                        _template_param(m["body"], 900)])
                except WaError as e2:
                    error = f"{e2.code}: {e2.message}"
            elif e.code == ERR_OUTSIDE_WINDOW:
                error = "עברו 24 שעות מההודעה האחרונה שלכם למספר העסקי — שלחו לו הודעה, או הגדירו תבנית"
            else:
                error = f"{e.code}: {e.message}"
        with self.svc.db() as c:
            c.execute("UPDATE support_messages SET wa_id = ?, wa_error = ? WHERE id = ?",
                      (wa_id, error[:200], message_id))
        self._mark(not error, error)
        if error:
            log.warning("whatsapp notify failed for message %s: %s", message_id, error)

    def verify_webhook(self, mode: str | None, token: str | None, challenge: str | None) -> str:
        cfg = self.wa_config()
        if mode == "subscribe" and cfg.verify_token and token and hmac.compare_digest(token, cfg.verify_token):
            return challenge or ""
        raise ApiError(403, "אימות נכשל")

    def handle_webhook(self, body: bytes, signature: str | None) -> list[tuple[str, str, str]]:
        """מקבל את ההודעות מהמנהל. מחזיר פעולות המשך לוואטסאפ: (סוג, למי, נתון) — לביצוע ברקע."""
        cfg = self.wa_config()
        if not cfg.can_receive or not verify_signature(cfg.app_secret, body, signature):
            raise ApiError(403, "חתימה לא תקינה")
        try:
            payload = json.loads(body)
        except ValueError as e:
            raise ApiError(400, "גוף לא תקין") from e
        follow: list[tuple[str, str, str]] = []
        for msg in parse_incoming(payload, cfg.phone_number_id):
            if msg.sender != cfg.admin_number:
                log.info("whatsapp message from an unknown number ignored")
                continue
            if msg.text is None:
                follow.append(("text", msg.sender, "אפשר לענות ללקוח רק בטקסט."))
                continue
            device_id, text = parse_target(msg.text)
            if device_id is None and msg.reply_to:
                with self.svc.db() as c:
                    r = c.execute("SELECT device_id FROM support_messages WHERE wa_id = ?", (msg.reply_to,)).fetchone()
                device_id = r["device_id"] if r else None
            if device_id is None:
                follow.append(("text", msg.sender, "כדי שהתשובה תגיע ללקוח: לחצו \"השב\" על ההודעה שלו, "
                                                  "או כתבו #מספר-מכשיר ואחריו את התשובה."))
                continue
            try:
                saved = self.reply(device_id, "whatsapp", text, via="whatsapp", wa_id=msg.wa_id)
            except ApiError as e:
                follow.append(("text", msg.sender, f"לא נשלח: {e.message}"))
                continue
            if saved is not None:
                follow.append(("react", msg.sender, msg.wa_id))
        return follow

    def run_followups(self, follow: list[tuple[str, str, str]]) -> None:
        if not follow:
            return
        cfg = self.wa_config()
        if not cfg.can_send:
            return
        client = self.client_factory(cfg)
        for kind, to, data in follow:
            try:
                if kind == "react":
                    client.react(to, data)
                else:
                    client.text(to, data)
            except WaError as e:
                log.warning("whatsapp follow-up %s failed: %s %s", kind, e.code, e.message)

    def send_test(self) -> dict:
        cfg = self.wa_config()
        if not cfg.can_send:
            raise ApiError(400, "חסרים טוקן, Phone number ID או המספר שלכם")
        try:
            self.client_factory(cfg).text(cfg.admin_number, "✅ FilterPhone: החיבור לוואטסאפ עובד. "
                                                            "פניות של לקוחות יגיעו לכאן.")
        except WaError as e:
            self._mark(False, f"{e.code}: {e.message}")
            raise ApiError(502, f"וואטסאפ החזיר שגיאה: {e.message}") from e
        self._mark(True)
        return {"ok": True}

    def prune(self, days: int = KEEP_DAYS) -> int:
        with self.svc.db() as c:
            return c.execute("DELETE FROM support_messages WHERE ts < ?", (db.now() - days * 86400,)).rowcount
