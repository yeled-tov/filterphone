"""API לשירות בוט הוואטסאפ הלא-רשמי (whatsapp-web.js).

הבוט רץ כשירות נפרד (deploy/whatsapp-bot) ומחובר לוואטסאפ של המנהל ב-QR. הוא פונה לשרת עם
טוקן בוט (wa.bot_token) כדי:
  * למשוך פניות של לקוחות מהאפליקציה ולשלוח אותן לוואטסאפ של המנהל (outbox).
  * להחזיר תשובות של המנהל (מהוואטסאפ) ללקוח באפליקציה.
  * לזהות לקוח לפי טלפון (CRM) כדי לענות עניינית.
  * לבצע פעולות *בטוחות* לבד (למשל חסימה), ולהעביר פעולות *קריטיות* (השהיה, פתיחה, הורדת רמה)
    לאישור המנהל — הבוט לעולם לא מבצע אותן לבד.

הרשאות מדורגות = הגנה: גם אם הבוט (או מי שמשוחח איתו) ינסה, פעולה קריטית רק נרשמת כבקשה.
"""
from __future__ import annotations

import hmac
import json
import logging

from . import db, security
from .catalog import CATALOG
from .service import ApiError, Services

log = logging.getLogger("filterphone.bot")

# פעולות שהבוט מבצע לבד = רק *הידוק* (חסימה נוספת). כל הקלה / פעולה הרסנית — רק באישור המנהל.
SAFE_OPTIONS = frozenset(CATALOG)            # הוספת כל חסימה מהקטלוג היא "בטוחה" (מהדקת)
CRITICAL_ACTIONS = ("pause", "unblock", "open", "level_down", "uninstall", "disable", "transfer")


def _topic(diag: str | None) -> str:
    """נושא הפנייה מהאפליקציה (למשל suggestion — הצעת ייעול, שהולכת ישר למנהל בלי מענה אוטומטי)."""
    try:
        t = json.loads(diag or "{}").get("topic", "")
    except (ValueError, AttributeError):
        return ""
    return t if isinstance(t, str) else ""


class BotDesk:
    def __init__(self, svc: Services):
        self.svc = svc

    def token(self) -> str:
        from .support import SupportDesk
        return SupportDesk(self.svc).wa_config().bot_token

    def authorized(self, token: str | None) -> bool:
        want = self.token()
        return bool(want) and bool(token) and hmac.compare_digest(token, want)

    # --- CRM: זיהוי לקוח לפי טלפון (לבוט, לענות עניינית) ---

    def customer(self, phone: str) -> dict:
        with self.svc.db() as c:
            cust = self.svc._customer_by_phone(c, phone)
            devices = c.execute(
                "SELECT id, name, level, status, policy FROM devices WHERE customer_id = ? ORDER BY id", (cust["id"],)
            ).fetchall()
        return {
            "customer": {"id": cust["id"], "name": cust["name"], "phone": security.normalize_phone(phone)},
            "devices": [self._device_summary(d) for d in devices],
        }

    def _device_summary(self, d) -> dict:
        from .catalog import DeviceSettings, summary
        settings = DeviceSettings.load(d["policy"])
        return {"device_id": d["id"], "name": d["name"], "level": d["level"], "status": d["status"],
                "blocks": [b["id"] for b in summary(settings)]}

    # --- העברת פניות מהאפליקציה לוואטסאפ של המנהל ---

    def outbox(self, after: int = 0, limit: int = 20) -> list[dict]:
        """פניות של לקוחות (מהאפליקציה) שעוד לא הועברו לוואטסאפ של המנהל (wa_id ריק)."""
        with self.svc.db() as c:
            rows = c.execute(
                "SELECT m.id, m.device_id, m.body, m.diag, d.name AS device_name, cu.name AS customer, cu.phone"
                " FROM support_messages m JOIN devices d ON d.id = m.device_id"
                " LEFT JOIN customers cu ON cu.id = d.customer_id"
                " WHERE m.sender = 'customer' AND m.via = 'app' AND m.wa_id = '' AND m.id > ?"
                " ORDER BY m.id LIMIT ?", (max(0, after), limit)).fetchall()
        return [{"id": r["id"], "device_id": r["device_id"], "device_name": r["device_name"],
                 "customer": r["customer"] or "", "phone": r["phone"] or "", "text": r["body"],
                 "topic": _topic(r["diag"])} for r in rows]

    def mark_forwarded(self, ids: list[int], wa_ids: dict[int, str] | None = None) -> int:
        wa_ids = wa_ids or {}
        n = 0
        with self.svc.db() as c, db.transaction(c):
            for mid in ids:
                if not isinstance(mid, int):
                    continue
                wid = security.clean_text(str(wa_ids.get(mid, f"bot:{mid}")), 200)
                n += c.execute("UPDATE support_messages SET wa_id = ? WHERE id = ? AND wa_id = ''", (wid, mid)).rowcount
        return n

    def reply(self, device_id: int, text: str, author: str = "whatsapp") -> dict:
        """המנהל ענה מהוואטסאפ → התשובה מגיעה ללקוח באפליקציה (דרך שולחן התמיכה)."""
        from .support import SupportDesk
        out = SupportDesk(self.svc).reply(device_id, author, text, via="whatsapp")
        if out is None:
            return {"ok": False, "reason": "duplicate"}
        return {"ok": True, "id": out["id"]}

    # --- פעולות מדורגות ---

    def block(self, device_id: int, options: list[str]) -> dict:
        """פעולה בטוחה: הוספת חסימות (מהדק). מוחל מיד. אפשרות לא מהקטלוג נדחית."""
        opts = sorted({o for o in options if o in SAFE_OPTIONS})
        if not opts:
            raise ApiError(400, "לא צוינו חסימות תקינות")
        row = self.svc.device_row(device_id)
        from .catalog import DeviceSettings
        current = set(DeviceSettings.load(row["policy"]).options)
        merged = sorted(current | set(opts))
        self.svc.update_device(device_id, {"options": merged}, admin="whatsapp-bot")
        added = [o for o in opts if o not in current]
        return {"ok": True, "added": added, "blocks": merged}

    def escalate(self, device_id: int, note: str, level: int | None = None,
                 options: list[str] | None = None) -> dict:
        """פעולה קריטית (השהיה / פתיחה / הורדת רמה): נרשמת כבקשה לאישור המנהל — הבוט לא מבצע לבד."""
        row = self.svc.device_row(device_id)
        text = security.clean_text(note, 1500, multiline=True) or "בקשה מהבוט (וואטסאפ)"
        return self.svc.device_request_change(row, level, options, f"[בוט וואטסאפ] {text}")
