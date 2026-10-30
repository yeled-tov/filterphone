"""עדכון הגדרות AdGuard Home (כש-AdGuard כבוי). בטוח להרצה חוזרת.

  * TLS כבוי — פורט 443 עובר ל-Caddy. הטלפונים משתמשים ב-DNS רגיל בתוך המנהרה.
  * חיפוש בטוח: Google, ‏YouTube, ‏Bing ועוד — מאולץ ברמת ה-DNS.
  * רשימת חסימה לשירותי DNS מוצפן / VPN / פרוקסי — כדי שאפליקציות לא יעקפו.
  * use-application-dns.net → NXDOMAIN: מסמן ל-Firefox לא להפעיל DNS מוצפן.
  * הגבלת קצב לכל טלפון בנפרד (‎/32), לא לכל רשת ה-VPN יחד — ראו _per_phone_ratelimit.
  * --api-user/--api-hash: משתמש נפרד ל-API של FilterPhone (חסימות DNS לפי מכשיר).
    המשתמשים הקיימים (של בעל השרת) לא משתנים. אם ל-AdGuard אין משתמשים בכלל — לא מוסיפים,
    כי זה היה מפעיל סיסמה על הממשק ונועל את בעל השרת בחוץ.
"""
from __future__ import annotations

import argparse

import yaml

BYPASS_LIST = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/doh-vpn-proxy-bypass.txt"
# תוכן למבוגרים — חסום לכל הטלפונים בכל רמה (גם בחיפוש: הפרוקסי מסתיר תוצאות לאתרים האלה)
NSFW_LIST = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/nsfw.txt"
GLOBAL_LISTS = ((BYPASS_LIST, "HaGeZi — עקיפות DNS/VPN/פרוקסי"), (NSFW_LIST, "HaGeZi — תוכן למבוגרים (NSFW)"))
CANARY_RULE = "||use-application-dns.net^$dnsrewrite=NXDOMAIN"
# חריגים גלובליים ש"תיקון ה-DNS" מ-12/10/2026 הוסיף ליוטיוב. בגלל $important הם גברו על כל חסימה —
# גם על חסימת YouTube שהמנהל בחר למכשיר מסוים. מוסרים (רק השורות המדויקות האלה; כל השאר נשאר).
_YOUTUBE_HOTFIX_HOSTS = (
    "youtube.com", "youtube-nocookie.com", "youtu.be", "googlevideo.com", "ytimg.com",
    "youtubei.googleapis.com", "youtube.googleapis.com", "youtubeembeddedplayer.googleapis.com",
    "youtube.co.il", "ggpht.com", "yt3.googleusercontent.com",
)
STALE_RULES = frozenset(f"@@||{h}^$important" for h in _YOUTUBE_HOTFIX_HOSTS)
# שאילתות בשנייה לכל טלפון. טלפון שה-VPN שלו עולה שולח בבת אחת עשרות שאילתות (כל אפליקציה מתחברת מחדש).
PHONE_RATELIMIT = 300


def find_key(node, key):
    """מחפש מילון שמכיל את המפתח, בכל עומק (המבנה משתנה בין גרסאות AdGuard)."""
    if isinstance(node, dict):
        if key in node:
            return node
        for v in node.values():
            found = find_key(v, key)
            if found is not None:
                return found
    return None


def _per_phone_ratelimit(doc: dict) -> None:
    """הגבלת הקצב של AdGuard נספרת כברירת מחדל לכל ‎/24 — וכל הטלפונים ב-VPN נמצאים ב-10.8.0.0/24.
    עם ratelimit: 30 (מ-5/10, נגד מתקפת הגברת DNS) כל הטלפונים יחד קיבלו 30 שאילתות בשנייה; מעבר לזה
    AdGuard זורק שאילתות בלי תשובה. טלפון שה-VPN שלו עולה חוצה את זה לבד — האפליקציות שואלות שוב ושוב,
    העומס נשאר מעל התקרה, ו"ה-VPN מחובר אבל אין אינטרנט" (גם לאפליקציה שלנו, אם היא בתוך המנהרה).
    היום פורט 53 פתוח רק מתוך המנהרה (firewall.sh), כך שההגנה ההיא לא נדרשת ברמת הרשת: סופרים לכל
    טלפון בנפרד (‎/32) עם תקרה נדיבה. 0 (בלי הגבלה) נשאר 0."""
    holder = find_key(doc, "ratelimit")
    if holder is None:
        return
    current = int(holder.get("ratelimit") or 0)
    if 0 < current < PHONE_RATELIMIT:
        holder["ratelimit"] = PHONE_RATELIMIT
    if "ratelimit_subnet_len_ipv4" in holder:      # גרסאות ישנות בלי המפתח — סופרות לכל כתובת ממילא
        holder["ratelimit_subnet_len_ipv4"] = 32


def main(path: str, api_user: str = "", api_hash: str = "") -> None:
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)

    doc.setdefault("tls", {})["enabled"] = False
    _per_phone_ratelimit(doc)

    holder = find_key(doc, "safe_search")
    if holder is not None and isinstance(holder["safe_search"], dict):
        holder["safe_search"]["enabled"] = True
        # כולל YouTube (מצב מוגבל). FilterTube לא מושפעת: היא מחוץ ל-VPN ולא משתמשת ב-DNS הזה.
        for engine in ("bing", "duckduckgo", "ecosia", "google", "pixabay", "yandex", "youtube"):
            holder["safe_search"][engine] = True

    filters = doc.setdefault("filters", []) or []
    for url, name in GLOBAL_LISTS:
        if not any(f.get("url") == url for f in filters):
            next_id = max([int(f.get("id", 0)) for f in filters] + [1000]) + 1
            filters.append({"enabled": True, "url": url, "id": next_id, "name": name})
    doc["filters"] = filters

    rules = doc.setdefault("user_rules", []) or []
    stale = [r for r in rules if str(r).strip().lower() in STALE_RULES]
    if stale:
        rules = [r for r in rules if str(r).strip().lower() not in STALE_RULES]
        print(f"  הוסרו {len(stale)} חריגי YouTube גלובליים (מתיקון 12/10) — חסימת YouTube לפי מכשיר חוזרת לעבוד")
    if CANARY_RULE not in rules:
        rules.append(CANARY_RULE)
    doc["user_rules"] = rules

    if api_user and api_hash:
        users = [u for u in (doc.get("users") or []) if isinstance(u, dict)]
        if not users:
            print("  AdGuard בלי משתמשים (בלי סיסמה) — לא מוסיף משתמש API")
        else:
            users = [u for u in users if u.get("name") != api_user]
            users.append({"name": api_user, "password": api_hash})
            doc["users"] = users
            print(f"  משתמש API: {api_user}")

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
    print("  AdGuard עודכן")


def api_url(path: str) -> str:
    """הכתובת שבה ה-API מגיע לממשק של AdGuard (לפי http.address, או bind_host/bind_port בגרסאות ישנות)."""
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    addr = (doc.get("http") or {}).get("address") or f"{doc.get('bind_host', '0.0.0.0')}:{doc.get('bind_port', 3000)}"
    host, _, port = str(addr).rpartition(":")
    host = host.strip("[]")
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{port}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--api-user", default="")
    ap.add_argument("--api-hash", default="")
    ap.add_argument("--print-url", action="store_true", help="רק להדפיס את כתובת הממשק, בלי לשנות כלום")
    a = ap.parse_args()
    if a.print_url:
        print(api_url(a.path))
    else:
        main(a.path, a.api_user, a.api_hash)
