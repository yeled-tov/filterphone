"""מצב התאימות של מכשיר: ירוק (מוגן) / צהוב (חלקי) / אדום (ההגנה לא פועלת).

מקורות המידע, מהאמין לפחות אמין:
  1. WireGuard (בשרת): handshake אחרון — אמת: המנהרה באמת עובדת.
  2. הדיווח התקופתי מהטלפון (heartbeat, כל 15 דקות): VPN, נגישות, מנהל מכשיר, סוללה, גרסת מדיניות.
     השרת גם יודע אם הדיווח הגיע דרך המנהרה (via_vpn) — דיווח שהגיע מבחוץ = הטלפון גולש בלי סינון.
  3. הדיווח הישן מהאפליקציה (guard) — לגרסאות שלפני ה-heartbeat.

שום דבר כאן לא "מנחש" לטובה: מה שלא דווח מסומן "לא ידוע", לא "תקין".
"""
from __future__ import annotations

from dataclasses import dataclass, field

GREEN, YELLOW, RED, INACTIVE = "green", "yellow", "red", "inactive"
ONLINE_WINDOW_S = 180           # WireGuard מחדש handshake כל 2 דקות כשיש תעבורה (keepalive כל 25 שניות)

# קוד → (חומרה, טקסט לממשק הניהול)
ISSUES: dict[str, tuple[str, str]] = {
    "vpn_off": (RED, "הטלפון מחובר לאינטרנט אבל לא דרך הסינון (ה-VPN כבוי)"),
    "accessibility_off": (RED, "מגן FilterPhone (נגישות) כבוי — חסימות בטלפון לא נאכפות"),
    "management_removed": (RED, "ניהול המכשיר (Device Owner) הוסר — ייתכן שהטלפון אופס"),
    "ca_missing": (RED, "תעודת הסינון לא מותקנת — תמונות באתרים מאובטחים לא מסוננות"),
    "lost": (RED, "אין שום קשר עם הטלפון יותר מיומיים"),
    "offline": (YELLOW, "לא מחובר כרגע (כבוי / בלי רשת)"),
    "policy_pending": (YELLOW, "שינוי במדיניות עוד לא הגיע לטלפון"),
    "battery_restricted": (YELLOW, "אופטימיזציית סוללה פעילה — המערכת עלולה לעצור את ההגנה ברקע"),
    "always_on_off": (YELLOW, "'VPN תמיד מופעל' כבוי — אחרי ניתוק הסינון לא חוזר לבד"),
    "lockdown_off": (YELLOW, "'חסימת חיבורים ללא VPN' כבויה — כשה-VPN נופל יש גלישה בלי סינון"),
    "private_dns": (YELLOW, "DNS פרטי מוגדר בטלפון — עוקף את הסינון ושובר את הגלישה"),
    "accessibility_stale": (YELLOW, "מגן FilterPhone מופעל בהגדרות אבל לא רץ (המערכת עצרה אותו)"),
    "no_heartbeat": (YELLOW, "גרסת אפליקציה ישנה — אין דיווח מהרקע, המצב לא ידוע"),
    "debugging": (YELLOW, "ניפוי באגים (ADB) פעיל במכשיר מנוהל"),
    "policy_error": (YELLOW, "הטלפון דיווח שלא הצליח להחיל את המדיניות"),
}


@dataclass
class Compliance:
    state: str
    online: bool
    vpn: str                    # up / down / unknown
    accessibility: str          # on / off / not_required / unknown
    management: str             # device_owner / device_admin / none / unknown
    policy_sync: str            # synced / syncing / pending / unknown
    last_contact: int | None
    issues: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"state": self.state, "online": self.online, "vpn": self.vpn, "accessibility": self.accessibility,
                "management": self.management, "policy_sync": self.policy_sync, "last_contact": self.last_contact,
                "issues": self.issues}


def _issue(code: str) -> dict:
    sev, text = ISSUES[code]
    return {"code": code, "severity": sev, "text": text}


def evaluate(row, hb: dict, guard: dict, handshake: int, needs_device_block: bool, needs_accessibility: bool,
             now: int, offline_after_s: int, lost_after_s: int, sync_grace_s: int) -> Compliance:
    """row: שורת devices. hb: ה-heartbeat האחרון (JSON). guard: הדיווח הישן. handshake: epoch או 0."""
    if row["status"] != "active":
        return Compliance(INACTIVE, False, "unknown", "unknown", "unknown", "unknown", row["last_seen_at"])

    hb_at = row["heartbeat_at"] or 0
    hb_fresh = bool(hb_at and now - hb_at < offline_after_s)
    hs_fresh = bool(handshake and now - handshake < ONLINE_WINDOW_S)
    contacts = [t for t in (hb_at, handshake, row["last_seen_at"]) if t]
    last_contact = max(contacts) if contacts else None
    online = hs_fresh or hb_fresh or bool(row["last_seen_at"] and now - row["last_seen_at"] < offline_after_s)
    issues: list[str] = []

    # --- VPN: WireGuard הוא האמת. דיווח טרי שהגיע דרך המנהרה = פעיל (גם אם הטלפון נרדם מאז —
    # במצב שינה אין תעבורה ואין handshake). דיווח טרי שהגיע מבחוץ, או שהטלפון אומר שה-VPN למטה = כבוי.
    hb_vpn_state = (hb.get("vpn") or {}).get("state") if isinstance(hb.get("vpn"), dict) else None
    if hs_fresh:
        vpn = "up"
    elif hb_fresh and (hb.get("via_vpn") is False or hb_vpn_state not in (None, "UP")):
        vpn = "down"
        issues.append("vpn_off")
    elif hb_fresh and hb.get("via_vpn") is True:
        vpn = "up"
    else:
        vpn = "unknown"

    # --- מנהל מכשיר
    device_owner = hb.get("device_owner", guard.get("device_owner"))
    if device_owner:
        management = "device_owner"
    elif hb.get("device_admin", guard.get("device_admin")):
        management = "device_admin"
    elif device_owner is False:
        management = "none"
    else:
        management = "unknown"
    if row["managed_at"] and device_owner is False:
        issues.append("management_removed")

    # --- נגישות: נדרשת למסכים / הגנות מיוחדות, ולחסימת אפליקציות כשאין מנהל מכשיר (שמסתיר אותן)
    acc_info = hb.get("accessibility") if isinstance(hb.get("accessibility"), dict) else {}
    acc_enabled = acc_info.get("enabled", guard.get("accessibility"))
    required = needs_accessibility or (needs_device_block and not device_owner)
    if not required:
        accessibility = "not_required" if acc_enabled is not True else "on"
    elif acc_enabled is True:
        accessibility = "on"
        if acc_info.get("running") is False:
            issues.append("accessibility_stale")
    elif acc_enabled is False:
        accessibility = "off"
        issues.append("accessibility_off")
    else:
        accessibility = "unknown"

    # --- שאר מה שהטלפון מדווח
    if hb.get("ca_installed", guard.get("ca_installed")) is False:
        issues.append("ca_missing")
    if hb.get("battery_unrestricted", guard.get("battery_unrestricted")) is False:
        issues.append("battery_restricted")
    vpn_hb = hb.get("vpn") if isinstance(hb.get("vpn"), dict) else {}
    always_on = vpn_hb.get("always_on", guard.get("always_on"))
    if always_on is False:
        issues.append("always_on_off")
    elif always_on is True and vpn_hb.get("lockdown", guard.get("lockdown")) is False:
        issues.append("lockdown_off")
    if hb.get("private_dns", guard.get("private_dns")) == "hostname":
        issues.append("private_dns")
    tamper = hb.get("tamper") if isinstance(hb.get("tamper"), dict) else {}
    if device_owner and tamper.get("adb"):
        issues.append("debugging")
    if hb.get("policy_error"):
        issues.append("policy_error")
    if not hb_at:
        issues.append("no_heartbeat")

    # --- סנכרון מדיניות
    server_v = row["policy_version"] or 1
    device_v = row["device_policy_version"]
    if device_v is None:
        policy_sync = "unknown"
    elif device_v >= server_v:
        policy_sync = "synced"
    elif now - (row["policy_updated_at"] or 0) < sync_grace_s:
        policy_sync = "syncing"
    elif hb.get("sync_mode") == "daily":
        # רענון יומי + בפתיחה/ברענון ידני (חיסכון סוללה): שינוי מהדשבורד ייושם בפתיחה הבאה או בתחזוקה
        # הלילית — זו לא תקלה, אז לא מסמנים צהוב.
        policy_sync = "deferred"
    else:
        policy_sync = "pending"
        issues.append("policy_pending")

    # --- קשר
    if not online:
        if not last_contact or now - last_contact >= lost_after_s:
            issues.append("lost")
        else:
            issues.append("offline")

    items = [_issue(c) for c in dict.fromkeys(issues)]
    state = RED if any(i["severity"] == RED for i in items) else YELLOW if items else GREEN
    return Compliance(state, online, vpn, accessibility, management, policy_sync, last_contact, items)
