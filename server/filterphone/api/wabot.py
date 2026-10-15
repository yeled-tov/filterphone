"""הגדרות בוט הוואטסאפ (מהדשבורד) והמצב שלו (מהבוט עצמו).

הבוט (deploy/whatsapp-bot) רץ בשרת כשירות fp-wabot ומושך מכאן את ההגדרות כל כמה שניות:
מופעל/כבוי, מוח ה-AI (Gemini / Groq + מפתח), מידע העסק (מחירים, כתובת, שעות) ושעות הפעילות.
הוא מדווח לכאן את המצב שלו — ממתין לסריקה (עם ה-QR), מחובר (באיזה מספר), שגיאה — והדשבורד מציג.
כך כל החיבור נעשה מהדשבורד: בלי טרמינל ובלי SSH.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import segno

from . import db
from .service import ApiError, Services

PROVIDERS = ("gemini", "groq", "none")
_SECRET = ("ai_key",)
_FIELDS = ("enabled", "ai_provider", "ai_key", "ai_model", "business_info", "hours", "friday_until",
           "handoff_minutes", "bot_name", "label_name", "auto_new", "include_contacts",
           "mark_ai", "ai_tag", "shadow")
STATUS_STALE_S = 120            # בלי דיווח מהבוט יותר מזה — "השירות לא רץ"

# מה שהבוט הישן ידע (מחירים, כתובת, שעות) — נקודת התחלה; עורכים בדשבורד
DEFAULT_BUSINESS = """FilterPhone — חברה ישראלית מאשדוד לסינון תוכן לטלפונים.
אתר: filterphone.com
כתובת: חטיבת גבעת שתיים, אשדוד.

אייפון: חסימת אתרים, פרסומות, רשתות חברתיות ואתרים ספציפיים. אפשר לחסום גוגל ואפסטור. אין יוטיוב כשר.

אנדרואיד:
1. כשר פליי — צריבה 70₪, מנוי 150₪ (לחיים/לשנה) עם שירות וחנות. דפדפן — תוספת 80₪ לשנה.
2. עסקן (גלקסי בלבד) — צריבה 300₪, מנוי 40₪ לחודש לשנה וחצי.
3. הדרן (הכי חזק, עלול לפגוע במכשיר) — צריבה 300₪, מנוי 40₪ לחודש לשנה וחצי.

מחיר מדויק, התקנה או הגעה — מתאמים עם נציג."""

DEFAULTS: dict = {
    "enabled": False,
    "ai_provider": "gemini",
    "ai_key": "",
    "ai_model": "",                     # ריק = הבוט בוחר לבד את המודל המהיר העדכני
    "business_info": DEFAULT_BUSINESS,
    "hours": "13:00-15:00, 20:00-22:30",
    "friday_until": "12:00",            # שבת — תמיד מחוץ לשעות
    "handoff_minutes": 60,              # בלי רשימה בוואטסאפ: כתבת בעצמך ללקוח → הבוט שותק בשיחה הזו
    "bot_name": "פילטר",
    # ניהול מתוך הוואטסאפ (ביזנס): שיחה ברשימה הזו — הבוט עונה; כתבת בעצמך — יוצאת ממנה
    "label_name": "בוט",
    "auto_new": True,                   # לקוח חדש (מעולם לא כתבת לו) — נכנס לרשימה אוטומטית
    "include_contacts": False,          # גם אנשי קשר שמורים בטלפון נחשבים "חדשים"
    "mark_ai": True,                    # להוסיף סימון בסוף כל הודעה שהבוט ענה (כמו הסימון של וואטסאפ)
    "ai_tag": "🤖 הודעה אוטומטית",       # הסימון עצמו
    # מצב השוואה: הבוט לא שולח כלום ללקוחות — רק רושם מה היה עונה, ליד מה שנענה בפועל (הבוט הקיים /
    # את/ה). ברירת מחדל: מופעל — כדי שלא יענו שני בוטים לאותו לקוח עד שמחליטים לעבור.
    "shadow": True,
}
COMPARE_KEEP = 500                      # כמה שורות השוואה לשמור (הישנות נמחקות)

_HOURS = re.compile(r"^\s*\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\s*(,\s*\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\s*)*$")


class WaBot:
    """הגדרות ב-meta (wabot.*), ומצב אחרון בזיכרון — הבוט מדווח כל כמה שניות."""

    _lock = threading.Lock()
    _status: dict = {}

    def __init__(self, svc: Services):
        self.svc = svc

    # ------------------------------------------------------------ הגדרות

    def settings(self) -> dict:
        with self.svc.db() as c:
            rows = {r["key"][6:]: r["value"] for r in
                    c.execute("SELECT key, value FROM meta WHERE key LIKE 'wabot.%'")}
        out = dict(DEFAULTS)
        for k, raw in rows.items():
            if k in _FIELDS:
                try:
                    out[k] = json.loads(raw)
                except ValueError:
                    out[k] = raw
        return out

    def save(self, admin: str, changes: dict) -> dict:
        rows: dict = {}
        for k in _FIELDS:
            if k not in changes or changes[k] is None:
                continue
            v = changes[k]
            if k in ("enabled", "auto_new", "include_contacts", "mark_ai", "shadow"):
                v = bool(v)
            elif k == "ai_provider":
                if v not in PROVIDERS:
                    raise ApiError(400, "ספק AI לא מוכר")
            elif k == "ai_key":
                v = str(v).strip()
                if not v:
                    continue                        # ריק = בלי שינוי
                if len(v) > 300 or re.search(r"\s", v):
                    raise ApiError(400, "מפתח לא תקין")
                provider = changes.get("ai_provider") or self.settings()["ai_provider"]
                if (provider == "gemini" and v.startswith("gsk_")) or (provider == "groq" and v.startswith("AIza")):
                    raise ApiError(400, "המפתח לא מתאים לספק שנבחר (Gemini מתחיל ב-AIza, Groq ב-gsk_)")
            elif k == "ai_model":
                v = str(v).strip()
                if v and not re.fullmatch(r"[\w.\-/]{1,80}", v):
                    raise ApiError(400, "שם מודל לא תקין")
            elif k == "business_info":
                v = str(v).strip()
                if len(v) > 6000:
                    raise ApiError(400, "מידע העסק ארוך מדי (עד 6000 תווים)")
            elif k == "hours":
                v = str(v).strip()
                if v and not _HOURS.match(v):
                    raise ApiError(400, "שעות: למשל 13:00-15:00, 20:00-22:30")
            elif k == "friday_until":
                v = str(v).strip()
                if v and not re.fullmatch(r"\d{1,2}:\d{2}", v):
                    raise ApiError(400, "שישי עד: למשל 12:00")
            elif k == "handoff_minutes":
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    raise ApiError(400, "דקות לא תקינות") from None
                if not 1 <= v <= 24 * 60:
                    raise ApiError(400, "דקות: 1–1440")
            elif k == "bot_name":
                v = str(v).strip()[:30]
            elif k == "ai_tag":
                v = str(v).strip()[:60]
            elif k == "label_name":
                v = str(v).strip()[:30]
                if not v:
                    raise ApiError(400, "שם הרשימה ריק (למשל: בוט)")
            rows[k] = v
        if changes.get("clear_key"):
            rows["ai_key"] = ""
        with self.svc.db() as c, db.transaction(c):
            for k, v in rows.items():
                c.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                          (f"wabot.{k}", json.dumps(v, ensure_ascii=False)))
        if rows:
            changed = ", ".join(sorted(k for k in rows if k not in _SECRET) + (["ai_key"] if "ai_key" in rows else []))
            self.svc.audit(admin, "wabot_config", "whatsapp-bot", changed[:200])
        return self.admin_view()

    def bot_config(self) -> dict:
        """מה שהבוט מקבל (כולל המפתח) + מספר הוואטסאפ של המנהל מהגדרות התמיכה."""
        from .support import SupportDesk
        s = self.settings()
        s["admin_number"] = SupportDesk(self.svc).wa_config().admin_number
        return s

    # ------------------------------------------------------------ מצב (מהבוט)

    def report(self, raw: dict) -> dict:
        state = str(raw.get("state", ""))[:20]
        if state not in ("off", "starting", "qr", "ready", "disconnected", "error"):
            raise ApiError(400, "מצב לא מוכר")
        st = {"state": state, "at": int(time.time()),
              "qr": str(raw.get("qr", ""))[:2000] if state == "qr" else "",
              "me": re.sub(r"\D", "", str(raw.get("me", "")))[:20],
              "error": str(raw.get("error", ""))[:300],
              "ai": str(raw.get("ai", ""))[:80],
              "version": str(raw.get("version", ""))[:20],
              "labels": str(raw.get("labels", "")) if raw.get("labels") in ("on", "missing", "not_business") else "",
              "label": str(raw.get("label", ""))[:30],
              "progress": max(0, min(100, int(raw["progress"]))) if isinstance(raw.get("progress"), int | float) else None,
              "log": [str(x)[:300] for x in (raw.get("log") or [])[-40:] if isinstance(x, str)],
              "stats": {k: int(v) for k, v in (raw.get("stats") or {}).items()
                        if isinstance(v, int | float) and re.fullmatch(r"[a-z_]{1,30}", str(k))}}
        with self._lock:
            WaBot._status = st
        return {"ok": True}

    def status(self) -> dict:
        with self._lock:
            st = dict(WaBot._status)
        if not st or time.time() - st.get("at", 0) > STATUS_STALE_S:
            return {"state": "not_running", "at": st.get("at"), "qr": "", "me": "", "error": "", "ai": "",
                    "labels": "", "label": "", "progress": None, "log": st.get("log", []), "stats": {}}
        return st

    def admin_view(self) -> dict:
        s = self.settings()
        key = s.pop("ai_key", "")
        st = self.status()
        qr = st.pop("qr", "")
        from .support import SupportDesk
        return {**s, "key_set": bool(key), "key_hint": f"…{key[-4:]}" if len(key) > 8 else "",
                "notify_to": SupportDesk(self.svc).wa_config().admin_number,
                "status": st,
                "qr_image": segno.make(qr, error="l").svg_data_uri(scale=6, border=2) if qr else ""}

    # ------------------------------------------------------------ מצב השוואה

    def compare_record(self, raw: dict) -> dict:
        """הבוט מדווח: שאלה של לקוח, מה הבוט שלנו היה עונה (ours), ומה נענה בפועל (theirs).
        שורה לכל הודעת לקוח (key = מזהה ההודעה); עדכונים מצטרפים לאותה שורה."""
        key = str(raw.get("key", "")).strip()[:200]
        if not key:
            raise ApiError(400, "חסר מזהה הודעה")
        now = int(time.time())
        phone = re.sub(r"\D", "", str(raw.get("phone", "")))[:20]
        question = str(raw.get("question") or "")[:2000]
        ours = str(raw.get("ours") or "")[:4000]
        theirs = str(raw.get("theirs") or "")[:4000]
        with self.svc.db() as c, db.transaction(c):
            row = c.execute("SELECT * FROM wabot_compare WHERE key = ?", (key,)).fetchone()
            if row is None:
                c.execute("INSERT INTO wabot_compare (key, phone, question, ours, theirs, ts, ours_at, theirs_at)"
                          " VALUES (?,?,?,?,?,?,?,?)",
                          (key, phone, question, ours, theirs, now, now if ours else None, now if theirs else None))
                c.execute("DELETE FROM wabot_compare WHERE id NOT IN"
                          " (SELECT id FROM wabot_compare ORDER BY id DESC LIMIT ?)", (COMPARE_KEEP,))
            else:
                # הבוט הקיים שלח כמה הודעות ברצף — מצטרפות לאותה תשובה
                joined = (row["theirs"] + "\n" + theirs).strip()[:4000] if theirs and row["theirs"] else (theirs or row["theirs"])
                c.execute("UPDATE wabot_compare SET phone = COALESCE(NULLIF(?, ''), phone),"
                          " question = COALESCE(NULLIF(?, ''), question), ours = COALESCE(NULLIF(?, ''), ours),"
                          " theirs = ?, ours_at = CASE WHEN ? != '' THEN ? ELSE ours_at END,"
                          " theirs_at = CASE WHEN ? != '' THEN ? ELSE theirs_at END WHERE id = ?",
                          (phone, question, ours, joined, ours, now, theirs, now, row["id"]))
        return {"ok": True}

    def compare_list(self, limit: int = 100) -> list[dict]:
        with self.svc.db() as c:
            rows = c.execute("SELECT * FROM wabot_compare ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))
            return [{"id": r["id"], "phone": r["phone"], "question": r["question"], "ours": r["ours"],
                     "theirs": r["theirs"], "ts": r["ts"], "ours_at": r["ours_at"], "theirs_at": r["theirs_at"]}
                    for r in rows]

    def compare_clear(self, admin: str) -> dict:
        with self.svc.db() as c, db.transaction(c):
            n = c.execute("DELETE FROM wabot_compare").rowcount
        self.svc.audit(admin, "wabot_compare_clear", "whatsapp-bot", str(n))
        return {"deleted": n}

    # ------------------------------------------------------------ בדיקת מפתח ה-AI

    def test_ai(self) -> dict:
        s = self.settings()
        key, provider = s["ai_key"], s["ai_provider"]
        if provider == "none":
            return {"ok": True, "message": "בלי AI — הבוט עונה תשובה קבועה ומעביר אליך"}
        if not key:
            return {"ok": False, "message": "עוד לא הוזן מפתח"}
        try:
            if provider == "gemini":
                url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200&key=" + urllib.parse.quote(key)
                data = _get_json(url, {})
                names = [m["name"].removeprefix("models/") for m in data.get("models", [])
                         if "generateContent" in m.get("supportedGenerationMethods", [])]
                model = s["ai_model"] or pick_gemini(names)
                return {"ok": bool(model), "message": f"המפתח תקין ✓ — המודל: {model}" if model else "אין מודל זמין"}
            data = _get_json("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {key}"})
            names = [m["id"] for m in data.get("data", [])]
            return {"ok": True, "message": f"המפתח תקין ✓ ({len(names)} מודלים זמינים)"}
        except urllib.error.HTTPError as e:
            return {"ok": False, "message": "המפתח שגוי" if e.code in (400, 401, 403) else f"שגיאה {e.code}"}
        except Exception as e:  # רשת
            return {"ok": False, "message": f"אין חיבור לספק: {type(e).__name__}"}


def import_legacy(svc: Services, groq: str = "", enable: bool = False) -> str:
    """מעבר מהבוט הישן (install.sh, פעם אחת): מפתח ה-Groq שלו, והפעלה אם החיבור לוואטסאפ הועבר.
    לא נוגע בהגדרות שכבר נשמרו בדשבורד."""
    bot = WaBot(svc)
    with svc.db() as c:
        configured = c.execute("SELECT 1 FROM meta WHERE key LIKE 'wabot.%' LIMIT 1").fetchone()
    if configured:
        return "הבוט כבר מוגדר בדשבורד — בלי שינוי"
    changes: dict = {}
    groq = (groq or "").strip()
    if groq and not re.search(r"\s", groq) and len(groq) <= 300:
        changes |= {"ai_provider": "groq", "ai_key": groq}
    if enable:
        changes["enabled"] = True
    if not changes:
        return "אין מה לייבא"
    bot.save("install", changes)
    return "יובא: " + ", ".join(sorted(k for k in changes if k != "ai_key") + (["ai_key"] if "ai_key" in changes else []))


def pick_gemini(names: list[str]) -> str:
    """המודל ה"flash" העדכני ביותר (מהיר וזול, מספיק לשירות לקוחות); בלי גרסאות ניסיוניות."""
    def ver(n: str) -> tuple:
        m = re.match(r"gemini-(\d+(?:\.\d+)?)-flash$", n)
        return (float(m.group(1)),) if m else (-1.0,)
    stable = [n for n in names if re.match(r"gemini-\d+(?:\.\d+)?-flash$", n)]
    if stable:
        return max(stable, key=ver)
    flash = [n for n in names if "flash" in n and "exp" not in n and "preview" not in n]
    return sorted(flash)[-1] if flash else (names[0] if names else "")


def _get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read(2_000_000))
