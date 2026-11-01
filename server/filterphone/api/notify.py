"""התראות לאפליקציית המנהל ("CRM FilterPhone"): מה חדש מאז הבדיקה הקודמת.

האפליקציה שואלת כל דקה בערך (GET /v1/admin/notifications?after=<סמן>) ומקבלת רק אירועים חדשים:
  • בקשת הצטרפות / שינוי שממתינה
  • הודעה מלקוח בצ'אט התמיכה (מקובץ לפי מכשיר — הודעה אחת עם הטקסט האחרון)
  • בקשה לבדיקת אתר שממתינה

הסמן הוא המזהה האחרון שנראה בכל טבלה ("r12.s340.w5") — עולה תמיד, בלי תלות בשעון. בלי סמן (התקנה
חדשה / כניסה מחדש) — מחזירים את הסמן הנוכחי בלי אירועים, כדי לא להציף בהיסטוריה.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from . import security

_CURSOR = re.compile(r"^r(\d{1,12})\.s(\d{1,12})\.w(\d{1,12})$")
MAX_EVENTS = 20
TEXT_LIMIT = 160

KIND_TITLES = {"join": "בקשת הצטרפות חדשה", "change": "בקשת שינוי חסימות"}


def parse_cursor(value: str | None) -> tuple[int, int, int] | None:
    m = _CURSOR.match((value or "").strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def _who(customer: str | None, device: str | None, device_id: int | None = None) -> str:
    name = security.clean_text(customer, 60)
    dev = security.clean_text(device, 60)
    if name and dev:
        return f"{name} · {dev}"
    return name or dev or (f"מכשיר #{device_id}" if device_id else "")


def admin_events(svc, after: str | None, allow: Callable[[str], bool] = lambda _area: True) -> dict:
    """{"cursor", "events": [...], "counts": {"requests", "support", "web"}} — ראו בראש הקובץ.
    allow(אזור) — הרשאות איש הצוות: אירועים ומונים רק מהאזורים שמותרים לו."""
    out = _events(svc, after)
    keep = {"requests": allow("requests"), "support": allow("support"), "web": allow("web")}
    area_of = {"join": "requests", "change": "requests", "support": "support", "web": "web"}
    out["events"] = [e for e in out["events"] if keep.get(area_of.get(e["kind"], ""), False)]
    out["counts"] = {k: (v if keep[k] else 0) for k, v in out["counts"].items()}
    return out


def _events(svc, after: str | None) -> dict:
    prev = parse_cursor(after)
    with svc.db() as c:
        top = tuple(c.execute(f"SELECT COALESCE(MAX(id), 0) FROM {t}").fetchone()[0]
                    for t in ("requests", "support_messages", "web_reviews"))
        counts = {
            "requests": c.execute("SELECT COUNT(*) FROM requests WHERE status = 'pending'").fetchone()[0],
            "support": c.execute("SELECT COUNT(DISTINCT device_id) FROM support_messages"
                                 " WHERE sender = 'customer' AND read_at IS NULL").fetchone()[0],
            "web": c.execute("SELECT COUNT(*) FROM web_reviews WHERE status = 'pending'").fetchone()[0],
        }
        cursor = f"r{top[0]}.s{top[1]}.w{top[2]}"
        if prev is None:
            return {"cursor": cursor, "events": [], "counts": counts}
        # מזהים נמחקו / מסד נתונים שוחזר (הסמן גדול מהקיים) — מתחילים מהמצב הנוכחי
        r0, s0, w0 = (p if p <= t else t for p, t in zip(prev, top, strict=True))
        events: list[dict] = []
        for r in c.execute(
                "SELECT r.id, r.kind, r.created_at, r.device_id, cu.name AS customer, d.name AS device"
                " FROM requests r LEFT JOIN customers cu ON cu.id = r.customer_id"
                " LEFT JOIN devices d ON d.id = r.device_id"
                " WHERE r.id > ? AND r.id <= ? AND r.status = 'pending' ORDER BY r.id LIMIT ?",
                (r0, top[0], MAX_EVENTS)):
            events.append({"kind": r["kind"], "key": f"r{r['id']}", "ts": r["created_at"],
                           "title": KIND_TITLES.get(r["kind"], "בקשה חדשה"),
                           "text": _who(r["customer"], r["device"], r["device_id"]), "route": "#/requests"})
        # צ'אט: הודעה אחת לכל מכשיר — הטקסט האחרון ומספר ההודעות החדשות
        chats: dict[int, dict] = {}
        for m in c.execute(
                "SELECT m.id, m.device_id, m.body, m.ts, d.name AS device, cu.name AS customer"
                " FROM support_messages m JOIN devices d ON d.id = m.device_id"
                " LEFT JOIN customers cu ON cu.id = d.customer_id"
                " WHERE m.id > ? AND m.id <= ? AND m.sender = 'customer' ORDER BY m.id",
                (s0, top[1])):
            chat = chats.setdefault(m["device_id"], {"n": 0})
            chat.update(n=chat["n"] + 1, body=m["body"], ts=m["ts"], who=_who(m["customer"], m["device"], m["device_id"]))
        for device_id, chat in list(chats.items())[-MAX_EVENTS:]:
            text = security.clean_text(chat["body"], TEXT_LIMIT) or "הודעה חדשה"
            if chat["n"] > 1:
                text = f"{text}  ({chat['n']} הודעות)"
            events.append({"kind": "support", "key": f"s{device_id}", "ts": chat["ts"],
                           "title": f"הודעה מ{chat['who']}" if chat["who"] else "הודעה חדשה בתמיכה",
                           "text": text, "route": f"#/support/{device_id}"})
        for w in c.execute(
                "SELECT w.id, w.host, w.created_at, w.device_id, d.name AS device FROM web_reviews w"
                " LEFT JOIN devices d ON d.id = w.device_id"
                " WHERE w.id > ? AND w.id <= ? AND w.status = 'pending' ORDER BY w.id LIMIT ?",
                (w0, top[2], MAX_EVENTS)):
            host = security.clean_text(w["host"], 80)
            dev = security.clean_text(w["device"], 60)
            events.append({"kind": "web", "key": f"w{w['id']}", "ts": w["created_at"],
                           "title": "בקשה לבדיקת אתר", "text": f"{host} · {dev}" if dev else host,
                           "route": "#/web"})
    return {"cursor": cursor, "events": events, "counts": counts}
