"""API לשירות בוט הוואטסאפ הלא-רשמי (whatsapp-web.js).

הבוט רץ כשירות נפרד (deploy/whatsapp-bot) ומחובר לוואטסאפ של המנהל ב-QR. הוא פונה לשרת עם
טוקן בוט (wa.bot_token) כדי:
  * למשוך פניות של לקוחות מהאפליקציה ולשלוח אותן לוואטסאפ של המנהל (outbox).
  * להחזיר תשובות של המנהל (מהוואטסאפ) ללקוח באפליקציה.
  * לזהות לקוח לפי טלפון (CRM) כדי לענות עניינית.
  * לבצע פעולות לבד, בגבולות קבועים:
      - חסימה (הידוק) — לצמיתות: אפליקציות/אתרים מהקטלוג, נקודה חמה ועוד הגבלות בטוחות.
      - פתיחה זמנית של דברים "קטנים" (חנות Play, נקודה חמה) — עד שעה, ואז נחסם שוב לבד.
  * כל השאר (השהיה, פתיחה קבועה, הורדת רמה) — רק כבקשה לאישור המנהל; הבוט לעולם לא מבצע לבד.

הרשאות מדורגות = הגנה: גם אם הבוט (או מי שמשוחח איתו) ינסה, פעולה קריטית רק נרשמת כבקשה.
"""
from __future__ import annotations

import hmac
import json
import logging

from . import db, security
from .catalog import CATALOG, DPC_ITEM_PREFIX, TEMP_OPEN_DEFAULT_MIN, TEMP_OPEN_MAX_MIN, item_title, temp_item
from .service import ApiError, Services

log = logging.getLogger("filterphone.bot")

# פעולות שהבוט מבצע לבד = רק *הידוק* (חסימה נוספת). כל הקלה / פעולה הרסנית — רק באישור המנהל.
SAFE_OPTIONS = frozenset(CATALOG)            # הוספת כל חסימה מהקטלוג היא "בטוחה" (מהדקת)
# הגבלות מנהל מכשיר שהבוט יכול להדליק לבד (הידוק בלבד; לא כאלה שמפריעות לתקן מרחוק או לחשבון Google)
SAFE_DPC_BLOCK = frozenset({"dpc:disallow_tethering", "dpc:disallow_unknown_sources", "dpc:disallow_add_user",
                            "dpc:disallow_private_dns"})
# מה הבוט רשאי לפתוח זמנית (עד TEMP_OPEN_MAX_MIN דקות, ואז נחסם שוב לבד). רק דברים "קטנים".
BOT_TEMP_OPEN = frozenset({"play_store", "galaxy_store", "dpc:disallow_tethering"})
CRITICAL_ACTIONS = ("pause", "unblock", "open", "level_down", "uninstall", "disable", "transfer")


def _diag(diag: str | None) -> dict:
    """ה-diag שהאפליקציה צירפה לפנייה (מצב טכני). מילון ריק אם אין / לא תקין."""
    try:
        d = json.loads(diag or "{}")
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def _topic(diag: str | None) -> str:
    """נושא הפנייה מהאפליקציה (למשל suggestion — הצעת ייעול, שהולכת ישר למנהל בלי מענה אוטומטי)."""
    t = _diag(diag).get("topic", "")
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

    def customer(self, phone: str, device: int | None = None, aid: str = "") -> dict:
        """הלקוח לפי מספר הוואטסאפ. אם המספר לא רשום — גם לפי "מכשיר #N" + מזהה אנדרואיד מההודעה שהאפליקציה
        מנסחת (רק מי שהטלפון בידו יודע את המזהה). selected = המכשיר שהבוט פועל עליו (או None — לשאול)."""
        from .support import device_uid_for
        with self.svc.db() as c:
            try:
                cust = self.svc._customer_by_phone(c, phone)
                devices = c.execute("SELECT * FROM devices WHERE customer_id = ? ORDER BY id", (cust["id"],)).fetchall()
            except ApiError:
                row = c.execute("SELECT * FROM devices WHERE id = ?", (device,)).fetchone() if device else None
                aid = (aid or "").strip().lower()
                if row is None or not aid or not row["device_uid"] or row["device_uid"] != device_uid_for(aid):
                    raise
                cust = c.execute("SELECT * FROM customers WHERE id = ?", (row["customer_id"],)).fetchone()
                if cust is None:
                    raise
                devices = [row]
        ids = [d["id"] for d in devices]
        selected = device if device in ids else (ids[0] if len(ids) == 1 else None)
        out = {
            "customer": {"id": cust["id"], "name": cust["name"], "phone": cust["phone"]},
            "devices": [self._device_summary(d) for d in devices],
            "selected": selected,
        }
        if selected is not None:
            out["actions"] = self.actions(next(d for d in devices if d["id"] == selected))
        return out

    def _device_summary(self, d) -> dict:
        from .catalog import DeviceSettings, summary
        settings = DeviceSettings.load(d["policy"])
        return {"device_id": d["id"], "name": d["name"], "level": d["level"], "status": d["status"],
                "blocks": [b["id"] for b in summary(settings)], "temp_open": self.svc.temp_opens(d)}

    def actions(self, row) -> dict:
        """מה הבוט יכול לעשות עכשיו במכשיר הזה — נשלח ל-AI כרשימה סגורה (מזהה + שם)."""
        opened = {o["item"] for o in self.svc.temp_opens(row)}
        can_open = [{"id": i, "title": item_title(i)} for i in sorted(BOT_TEMP_OPEN)
                    if self.svc.is_blocked(row, i) and i not in opened]
        can_block = [{"id": o, "title": CATALOG[o].title} for o in sorted(SAFE_OPTIONS)
                     if not self.svc.is_blocked(row, o)]
        can_block += [{"id": i, "title": item_title(i)} for i in sorted(SAFE_DPC_BLOCK)
                      if not self.svc.is_blocked(row, i)]
        return {"open": can_open, "block": can_block, "opened": sorted(opened),
                "max_minutes": TEMP_OPEN_MAX_MIN, "default_minutes": TEMP_OPEN_DEFAULT_MIN}

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
                 "topic": _topic(r["diag"]), "diag": _diag(r["diag"])} for r in rows]

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
        """פעולה בטוחה: הוספת חסימות (מהדק) — לצמיתות, מוחל מיד. אפשרויות מהקטלוג והגבלות מנהל מכשיר
        בטוחות (למשל hotspot = נקודה חמה). משהו אחר נדחה. פתיחה זמנית פעילה של אותו דבר — נסגרת."""
        items = {temp_item(o) for o in options}
        opts = sorted(i for i in items if i in SAFE_OPTIONS)
        dpc = sorted(i for i in items if i in SAFE_DPC_BLOCK)
        if not opts and not dpc:
            raise ApiError(400, "לא צוינו חסימות תקינות")
        row = self.svc.device_row(device_id)
        from .catalog import DeviceSettings
        settings = DeviceSettings.load(row["policy"])
        added = [i for i in opts + dpc if not self.svc.is_blocked(row, i)]
        changes: dict = {}
        if opts:
            changes["options"] = sorted(set(settings.options) | set(opts))
        if dpc:
            changes["dpc"] = {i[len(DPC_ITEM_PREFIX):]: True for i in dpc}
        self.svc.update_device(device_id, changes, admin="whatsapp-bot")
        for i in opts + dpc:
            if i in settings.temp_open:
                self.svc.temp_close(device_id, i, "whatsapp-bot")
        return {"ok": True, "added": added, "titles": [item_title(i) for i in added],
                "blocks": changes.get("options", settings.options)}

    def open(self, device_id: int, item: str, minutes: int | None = None) -> dict:
        """פתיחה זמנית — רק מה שברשימת BOT_TEMP_OPEN, ועד TEMP_OPEN_MAX_MIN דקות. אחר כך נחסם שוב לבד."""
        it = temp_item(item)
        if it not in BOT_TEMP_OPEN:
            raise ApiError(403, "את זה הבוט לא פותח לבד — צריך אישור מנהל")
        return self.svc.temp_open(device_id, it, minutes or TEMP_OPEN_DEFAULT_MIN, "whatsapp-bot")

    def close(self, device_id: int, item: str) -> dict:
        return self.svc.temp_close(device_id, item, "whatsapp-bot")

    def escalate(self, device_id: int, note: str, level: int | None = None,
                 options: list[str] | None = None) -> dict:
        """פעולה קריטית (השהיה / פתיחה / הורדת רמה): נרשמת כבקשה לאישור המנהל — הבוט לא מבצע לבד."""
        row = self.svc.device_row(device_id)
        text = security.clean_text(note, 1500, multiline=True) or "בקשה מהבוט (וואטסאפ)"
        return self.svc.device_request_change(row, level, options, f"[בוט וואטסאפ] {text}")
