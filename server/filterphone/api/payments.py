"""תשלומים: מחירון, קישורי תשלום ללקוחות, קבלת אישור מהספק (webhook) ועדכון "שולם עד".

הבנייה כללית — כל ספק סליקה (EZcount / Grow / אחר) הוא "מתאם" אחד (Provider) עם שתי פעולות:
  checkout()       — יוצר אצל הספק דף / קישור תשלום לתשלום שלנו, ומחזיר את הכתובת ואת המזהה שלו.
  parse_webhook()  — מאמת את ההודעה שהספק שולח אחרי תשלום (חתימה), ומתרגם אותה לאירוע אחיד.
כל השאר (מחירון, קישור קצר ללקוח, אידמפוטנטיות, הארכת "שולם עד", הודעה ללקוח, יומן) — כאן, משותף.

עד שהעסק נפתח ויש מפתחות API: ספק "בדיקה" (test) — אותו מסלול בדיוק בלי כסף אמיתי: הקישור פותח
דף בדיקה, ולחיצה על "תשלום הצליח" שולחת webhook חתום כמו ספק אמיתי. חיבור ספק אמיתי = מחלקה אחת
ב-PROVIDERS + המפתחות בדשבורד.

סכומים — באגורות (int), כדי שלא יהיו שגיאות עיגול.
"""
from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from . import db, security
from .service import ApiError, Services

INTERVALS = ("once", "month", "year")
STATUSES = ("pending", "paid", "failed", "cancelled", "refunded")
_SECRET_FIELDS = ("api_key", "api_secret", "webhook_secret")


# ------------------------------------------------------------------ מתאמי ספקים

@dataclass(slots=True)
class Checkout:
    pay_url: str            # לאן לשלוח את הלקוח (דף התשלום של הספק)
    provider_ref: str = ""  # מזהה העסקה / הדף אצל הספק


@dataclass(slots=True)
class Event:
    event_id: str           # מזהה ייחודי של ההודעה (אותה הודעה פעמיים — מטופלת פעם אחת)
    status: str             # paid / failed / refunded / cancelled
    token: str = ""         # המזהה שלנו (אם הספק מחזיר אותו)
    provider_ref: str = ""
    amount: int | None = None   # באגורות — לבדיקה מול הסכום שביקשנו
    receipt_url: str = ""
    receipt_number: str = ""


class Provider:
    """ממשק לספק סליקה. מחלקה חדשה לכל ספק — עם הפרטים מהתיעוד שלו."""
    name = ""
    title = ""
    # שדות ההגדרה שהמנהל ממלא בדשבורד: (מפתח, תווית, סודי?)
    fields: tuple[tuple[str, str, bool], ...] = ()

    def __init__(self, settings: dict):
        self.settings = settings

    def checkout(self, payment: dict, customer: dict, urls: dict) -> Checkout:
        raise NotImplementedError

    def parse_webhook(self, headers: dict, body: bytes) -> Event:
        raise NotImplementedError


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestProvider(Provider):
    """מצב בדיקה: בלי כסף אמיתי. דף תשלום שלנו; "הצליח / נכשל" שולח webhook חתום (HMAC) — אותו מסלול
    בדיוק כמו ספק אמיתי (אימות, אידמפוטנטיות, הארכת התוקף, הודעה ללקוח)."""
    name = "test"
    title = "מצב בדיקה — בלי כסף אמיתי"

    def checkout(self, payment: dict, customer: dict, urls: dict) -> Checkout:
        return Checkout(pay_url=f"{urls['base']}/v1/pay/{payment['token']}/test", provider_ref=f"test-{payment['token']}")

    def parse_webhook(self, headers: dict, body: bytes) -> Event:
        secret = self.settings.get("webhook_secret", "")
        got = headers.get("x-fp-signature", "")
        if not secret or not hmac.compare_digest(got, sign(secret, body)):
            raise ApiError(401, "חתימה לא תקינה")
        try:
            d = json.loads(body)
        except ValueError as e:
            raise ApiError(400, "גוף לא תקין") from e
        return Event(event_id=str(d.get("event_id", "")), status=str(d.get("status", "")),
                     token=str(d.get("token", "")), provider_ref=str(d.get("provider_ref", "")),
                     amount=d.get("amount") if isinstance(d.get("amount"), int) else None,
                     receipt_url=str(d.get("receipt_url", "")), receipt_number=str(d.get("receipt_number", "")))


# ספקים זמינים. EZcount / Grow יתווספו כאן כשיהיו מפתחות ותיעוד (מחלקה עם checkout + parse_webhook).
PROVIDERS: dict[str, type[Provider]] = {"test": TestProvider}
PROVIDER_CHOICES = {"none": "לא מחובר", **{k: v.title for k, v in PROVIDERS.items()}}


def add_months(ts: int, months: int) -> int:
    """ts + [months] חודשים קלנדריים (31.1 + חודש = 28/29.2). טהור."""
    d = datetime.fromtimestamp(ts, UTC)
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return int(d.replace(year=y, month=m, day=day).timestamp())


def shekels(agorot: int) -> str:
    """1500 → '15', 1590 → '15.90'. טהור."""
    return f"{agorot // 100}" if agorot % 100 == 0 else f"{agorot / 100:.2f}"


# ------------------------------------------------------------------ השירות

class Payments:
    def __init__(self, svc: Services):
        self.svc = svc

    # ---------------------------------------------------- הגדרות (meta: payments.*)

    def settings(self) -> dict:
        with self.svc.db() as c:
            rows = {r["key"][9:]: r["value"] for r in
                    c.execute("SELECT key, value FROM meta WHERE key LIKE 'payments.%'")}
        out = {"enabled": False, "provider": "none", "business_name": "FilterPhone", "credentials": {},
               "webhook_secret": ""}
        for k, raw in rows.items():
            try:
                out[k] = json.loads(raw)
            except ValueError:
                pass
        return out

    def _put(self, c, key: str, value) -> None:
        c.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (f"payments.{key}", json.dumps(value, ensure_ascii=False)))

    def ensure_webhook_secret(self) -> str:
        s = self.settings()
        if s["webhook_secret"]:
            return s["webhook_secret"]
        secret = secrets.token_urlsafe(32)
        with self.svc.db() as c, db.transaction(c):
            self._put(c, "webhook_secret", secret)
        return secret

    def save_settings(self, admin: str, changes: dict) -> dict:
        cur = self.settings()
        with self.svc.db() as c, db.transaction(c):
            if "enabled" in changes and changes["enabled"] is not None:
                self._put(c, "enabled", bool(changes["enabled"]))
            if changes.get("provider") is not None:
                if changes["provider"] not in PROVIDER_CHOICES:
                    raise ApiError(400, "ספק לא מוכר")
                self._put(c, "provider", changes["provider"])
            if changes.get("business_name") is not None:
                self._put(c, "business_name", security.clean_text(changes["business_name"], 60) or "FilterPhone")
            if isinstance(changes.get("credentials"), dict):
                creds = dict(cur.get("credentials") or {})
                for k, v in changes["credentials"].items():
                    if not re.fullmatch(r"[a-z_]{1,40}", str(k)) or not isinstance(v, str):
                        continue
                    v = v.strip()
                    if v == "" and k in _SECRET_FIELDS:
                        continue                    # ריק = בלי שינוי (כמו מפתח הבוט)
                    if len(v) > 500:
                        raise ApiError(400, "ערך ארוך מדי")
                    creds[k] = v
                self._put(c, "credentials", creds)
        self.ensure_webhook_secret()
        self.svc.audit(admin, "payments_settings", "payments", ",".join(sorted(k for k in changes if changes[k] is not None)))
        return self.admin_view()

    def admin_view(self) -> dict:
        s = self.settings()
        provider = s.get("provider", "none")
        creds = s.get("credentials") or {}
        return {"enabled": bool(s["enabled"]), "provider": provider, "business_name": s["business_name"],
                "providers": PROVIDER_CHOICES,
                "fields": [{"key": k, "label": label, "secret": secret,
                            "value": "" if secret else creds.get(k, ""), "set": bool(creds.get(k))}
                           for k, label, secret in (PROVIDERS[provider].fields if provider in PROVIDERS else ())],
                "webhook_url": f"https://{self.svc.cfg.public_host}/v1/payments/webhook/{provider}"
                if provider in PROVIDERS else "",
                "ready": bool(s["enabled"]) and provider in PROVIDERS}

    def provider(self) -> Provider:
        s = self.settings()
        cls = PROVIDERS.get(s.get("provider", "none"))
        if not s["enabled"] or cls is None:
            raise ApiError(409, "הסליקה עוד לא מחוברת — דשבורד ← תשלומים ← חיבור הסליקה")
        return cls({**(s.get("credentials") or {}), "webhook_secret": self.ensure_webhook_secret()})

    # ---------------------------------------------------- מחירון

    @staticmethod
    def _plan(r) -> dict:
        return {"id": r["id"], "code": r["code"], "title": r["title"], "amount": r["amount"],
                "interval": r["interval"], "months": r["months"], "enabled": bool(r["enabled"])}

    def plans(self, enabled_only: bool = False) -> list[dict]:
        with self.svc.db() as c:
            q = "SELECT * FROM plans" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY amount, id"
            return [self._plan(r) for r in c.execute(q)]

    def save_plan(self, admin: str, data: dict, plan_id: int | None = None) -> dict:
        title = security.clean_text(data.get("title") or "", 80)
        code = str(data.get("code") or "").strip().lower()
        if not title:
            raise ApiError(400, "חסר שם למסלול")
        if not re.fullmatch(r"[a-z0-9_-]{2,30}", code):
            raise ApiError(400, "קוד מסלול: אותיות באנגלית / ספרות, למשל monthly")
        try:
            amount = int(data.get("amount"))
        except (TypeError, ValueError):
            raise ApiError(400, "סכום לא תקין") from None
        if not 100 <= amount <= 10_000_000:
            raise ApiError(400, "סכום: בין 1 ₪ ל-100,000 ₪")
        interval = data.get("interval") or "once"
        if interval not in INTERVALS:
            raise ApiError(400, "סוג תשלום לא מוכר")
        months = int(data.get("months") or {"once": 0, "month": 1, "year": 12}[interval])
        if not 0 <= months <= 120:
            raise ApiError(400, "חודשים: 0–120")
        enabled = 0 if data.get("enabled") is False else 1
        t = db.now()
        with self.svc.db() as c, db.transaction(c):
            dup = c.execute("SELECT id FROM plans WHERE code = ? AND id != ?", (code, plan_id or 0)).fetchone()
            if dup:
                raise ApiError(409, "כבר יש מסלול עם הקוד הזה")
            if plan_id is None:
                plan_id = c.execute("INSERT INTO plans (code, title, amount, interval, months, enabled, created_at,"
                                    " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                                    (code, title, amount, interval, months, enabled, t, t)).lastrowid
            elif c.execute("UPDATE plans SET code = ?, title = ?, amount = ?, interval = ?, months = ?, enabled = ?,"
                           " updated_at = ? WHERE id = ?",
                           (code, title, amount, interval, months, enabled, t, plan_id)).rowcount == 0:
                raise ApiError(404, "מסלול לא נמצא")
            row = c.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        self.svc.audit(admin, "plan_save", f"plan:{plan_id}", f"{code} {amount}")
        return self._plan(row)

    # ---------------------------------------------------- תשלומים

    @staticmethod
    def _payment(r) -> dict:
        d = {k: r[k] for k in r.keys()}
        d["link"] = ""
        return d

    def _link(self, token: str) -> str:
        return f"https://{self.svc.cfg.public_host}/v1/pay/{token}"

    def create_payment(self, admin: str, *, customer_id: int | None = None, phone: str = "",
                       device_id: int | None = None, plan_code: str = "", amount: int | None = None,
                       description: str = "") -> dict:
        """קישור תשלום ללקוח: לפי מסלול מהמחירון, או סכום ותיאור חופשיים (חד-פעמי)."""
        provider = self.provider()
        with self.svc.db() as c:
            cust = None
            if customer_id:
                cust = c.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
            elif device_id:
                cust = c.execute("SELECT c.* FROM customers c JOIN devices d ON d.customer_id = c.id WHERE d.id = ?",
                                 (device_id,)).fetchone()
            elif phone:
                digits = re.sub(r"\D", "", phone)
                cust = c.execute("SELECT * FROM customers WHERE replace(replace(phone, '-', ''), ' ', '') = ?"
                                 " ORDER BY id DESC LIMIT 1", (digits,)).fetchone()
            if cust is None:
                raise ApiError(404, "לקוח לא נמצא")
            if device_id is None:
                dev = c.execute("SELECT id FROM devices WHERE customer_id = ? AND status = 'active' ORDER BY id DESC"
                                " LIMIT 1", (cust["id"],)).fetchone()
                device_id = dev["id"] if dev else None
            plan = None
            if plan_code:
                plan = c.execute("SELECT * FROM plans WHERE code = ? AND enabled = 1", (plan_code,)).fetchone()
                if plan is None:
                    raise ApiError(404, "מסלול לא נמצא במחירון")
        if plan is not None:
            amount, desc = plan["amount"], description or plan["title"]
        else:
            if not isinstance(amount, int) or not 100 <= amount <= 10_000_000:
                raise ApiError(400, "סכום: בין 1 ₪ ל-100,000 ₪ (או מסלול מהמחירון)")
            desc = security.clean_text(description, 120)
            if not desc:
                raise ApiError(400, "חסר תיאור לתשלום (מה משלמים)")
        token = secrets.token_urlsafe(12)
        t = db.now()
        with self.svc.db() as c, db.transaction(c):
            pid = c.execute(
                "INSERT INTO payments (customer_id, device_id, plan_code, description, amount, status, provider, token,"
                " created_by, created_at, updated_at) VALUES (?,?,?,?,?, 'pending', ?, ?, ?, ?, ?)",
                (cust["id"], device_id, plan["code"] if plan else "", desc, amount, provider.name, token, admin, t, t)
            ).lastrowid
        row = self._get(pid)
        try:
            co = provider.checkout(dict(row), dict(cust), {"base": f"https://{self.svc.cfg.public_host}",
                                                           "link": self._link(token)})
        except ApiError:
            self._set(pid, status="failed")
            raise
        self._set(pid, pay_url=co.pay_url, provider_ref=co.provider_ref)
        self.svc.audit(admin, "payment_create", f"payment:{pid}", f"customer:{cust['id']} {amount} {desc}"[:200])
        return self.view(pid)

    def _get(self, pid: int):
        with self.svc.db() as c:
            row = c.execute("SELECT * FROM payments WHERE id = ?", (pid,)).fetchone()
        if row is None:
            raise ApiError(404, "תשלום לא נמצא")
        return row

    def _set(self, pid: int, **fields) -> None:
        with self.svc.db() as c, db.transaction(c):
            c.execute(f"UPDATE payments SET {', '.join(f'{k} = ?' for k in fields)}, updated_at = ? WHERE id = ?",
                      (*fields.values(), db.now(), pid))

    def view(self, pid: int) -> dict:
        r = self._get(pid)
        with self.svc.db() as c:
            cust = c.execute("SELECT name, phone FROM customers WHERE id = ?", (r["customer_id"],)).fetchone() \
                if r["customer_id"] else None
        d = self._payment(r)
        d["link"] = self._link(r["token"])
        d["customer_name"] = cust["name"] if cust else ""
        d["phone"] = cust["phone"] if cust else ""
        d["wa_text"] = (f"שלום{' ' + cust['name'] if cust and cust['name'] else ''}, לתשלום עבור {r['description']}"
                        f" ({shekels(r['amount'])} ₪): {d['link']}\nתודה, {self.settings()['business_name']}")
        return d

    def list_payments(self, customer_id: int | None = None, status: str = "", limit: int = 100) -> list[dict]:
        q = ("SELECT p.*, c.name AS customer_name, c.phone FROM payments p LEFT JOIN customers c ON c.id = p.customer_id")
        where, args = [], []
        if customer_id:
            where.append("p.customer_id = ?")
            args.append(customer_id)
        if status in STATUSES:
            where.append("p.status = ?")
            args.append(status)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY p.id DESC LIMIT ?"
        args.append(max(1, min(limit, 500)))
        with self.svc.db() as c:
            out = []
            for r in c.execute(q, args):
                d = self._payment(r)
                d["link"] = self._link(r["token"])
                out.append(d)
            return out

    def cancel(self, admin: str, pid: int) -> dict:
        r = self._get(pid)
        if r["status"] != "pending":
            raise ApiError(409, "אפשר לבטל רק תשלום שממתין")
        self._set(pid, status="cancelled")
        self.svc.audit(admin, "payment_cancel", f"payment:{pid}", "")
        return self.view(pid)

    def mark_paid(self, admin: str, pid: int, note: str = "") -> dict:
        """שולם בדרך אחרת (מזומן / העברה / ביט ידני) — אותה הארכת תוקף, בלי ספק."""
        r = self._get(pid)
        if r["status"] not in ("pending", "failed"):
            raise ApiError(409, "התשלום כבר טופל")
        self._apply(r, Event(event_id=f"manual-{pid}-{time.time_ns()}", status="paid",
                             receipt_number=security.clean_text(note, 60)), manual_by=admin)
        return self.view(pid)

    # ---------------------------------------------------- אישור מהספק

    def handle_webhook(self, provider_name: str, headers: dict, body: bytes) -> dict:
        s = self.settings()
        cls = PROVIDERS.get(provider_name)
        if cls is None or s.get("provider") != provider_name:
            raise ApiError(404, "ספק לא מחובר")
        ev = cls({**(s.get("credentials") or {}), "webhook_secret": self.ensure_webhook_secret()}).parse_webhook(
            {k.lower(): v for k, v in headers.items()}, body)
        if not ev.event_id or ev.status not in ("paid", "failed", "refunded", "cancelled"):
            raise ApiError(400, "אירוע לא מוכר")
        with self.svc.db() as c, db.transaction(c):
            try:
                c.execute("INSERT INTO payment_events (provider, event_id, kind, raw, created_at) VALUES (?,?,?,?,?)",
                          (provider_name, ev.event_id[:120], ev.status, body[:4000].decode("utf-8", "replace"),
                           db.now()))
            except Exception:                   # UNIQUE — כבר טופל (ספקים שולחים שוב עד שמקבלים 200)
                return {"ok": True, "duplicate": True}
            row = None
            if ev.token:
                row = c.execute("SELECT * FROM payments WHERE token = ?", (ev.token,)).fetchone()
            if row is None and ev.provider_ref:
                row = c.execute("SELECT * FROM payments WHERE provider = ? AND provider_ref = ?",
                                (provider_name, ev.provider_ref)).fetchone()
        if row is None:
            raise ApiError(404, "תשלום לא נמצא")
        if ev.amount is not None and ev.status == "paid" and ev.amount != row["amount"]:
            self.svc.audit("payments", "payment_amount_mismatch", f"payment:{row['id']}",
                           f"expected {row['amount']} got {ev.amount}")
            raise ApiError(400, "הסכום לא תואם")
        self._apply(row, ev)
        return {"ok": True}

    def _apply(self, row, ev: Event, manual_by: str = "") -> None:
        """מעבר מצב + השפעות: שולם → "שולם" ללקוח והארכת "בתוקף עד" לפי המסלול, והודעה ללקוח באפליקציה."""
        if row["status"] == ev.status or (row["status"] == "paid" and ev.status in ("failed", "cancelled")):
            return                              # כבר במצב הזה / תשלום שהצליח לא "נכשל" בדיעבד
        t = db.now()
        with self.svc.db() as c, db.transaction(c):
            c.execute("UPDATE payments SET status = ?, paid_at = CASE WHEN ? = 'paid' THEN ? ELSE paid_at END,"
                      " receipt_url = CASE WHEN ? != '' THEN ? ELSE receipt_url END,"
                      " receipt_number = CASE WHEN ? != '' THEN ? ELSE receipt_number END, updated_at = ?"
                      " WHERE id = ?",
                      (ev.status, ev.status, t, ev.receipt_url, ev.receipt_url[:500], ev.receipt_number,
                       ev.receipt_number[:60], t, row["id"]))
            if ev.status == "paid" and row["customer_id"]:
                cust = c.execute("SELECT paid_until FROM customers WHERE id = ?", (row["customer_id"],)).fetchone()
                plan = c.execute("SELECT months FROM plans WHERE code = ?", (row["plan_code"],)).fetchone() \
                    if row["plan_code"] else None
                months = plan["months"] if plan else 0
                until = cust["paid_until"] if cust else None
                if months:
                    until = add_months(max(t, until or 0), months)
                c.execute("UPDATE customers SET payment = 'paid', paid_until = ? WHERE id = ?",
                          (until, row["customer_id"]))
                if row["device_id"]:
                    receipt = f"\nקבלה: {ev.receipt_url}" if ev.receipt_url.startswith("https://") else ""
                    valid = (f"\nהמנוי בתוקף עד {datetime.fromtimestamp(until, UTC).strftime('%d/%m/%Y')}."
                             if months and until else "")
                    self.svc._tell_device(c, row["device_id"],
                                          f"✅ התשלום התקבל — {row['description']} ({shekels(row['amount'])} ₪). תודה!"
                                          f"{valid}{receipt}")
            self.svc.audit(manual_by or "payments", f"payment_{ev.status}", f"payment:{row['id']}",
                           f"{row['amount']} {ev.receipt_number}".strip(), c)

    # ---------------------------------------------------- דף התשלום (הקישור ששולחים ללקוח)

    def resolve(self, token: str) -> dict:
        with self.svc.db() as c:
            row = c.execute("SELECT * FROM payments WHERE token = ?", (token,)).fetchone()
        if row is None:
            raise ApiError(404, "קישור התשלום לא נמצא")
        return dict(row)

    def simulate(self, token: str, ok: bool) -> dict:
        """מצב בדיקה בלבד: דף הבדיקה "משלם" — webhook חתום, בדיוק כמו ספק אמיתי."""
        if self.settings().get("provider") != "test":
            raise ApiError(404, "לא במצב בדיקה")
        row = self.resolve(token)
        body = json.dumps({"event_id": f"test-{token}-{time.time_ns()}", "status": "paid" if ok else "failed",
                           "token": token, "amount": row["amount"],
                           "receipt_number": f"TEST-{row['id']}" if ok else ""}).encode()
        return self.handle_webhook("test", {"x-fp-signature": sign(self.ensure_webhook_secret(), body)}, body)
