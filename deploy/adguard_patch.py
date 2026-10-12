"""עדכון הגדרות AdGuard Home (כש-AdGuard כבוי). בטוח להרצה חוזרת.

  * TLS כבוי — פורט 443 עובר ל-Caddy. הטלפונים משתמשים ב-DNS רגיל בתוך המנהרה.
  * חיפוש בטוח: Google, ‏YouTube, ‏Bing ועוד — מאולץ ברמת ה-DNS.
  * רשימת חסימה לשירותי DNS מוצפן / VPN / פרוקסי — כדי שאפליקציות לא יעקפו.
  * use-application-dns.net → NXDOMAIN: מסמן ל-Firefox לא להפעיל DNS מוצפן.
  * --api-user/--api-hash: משתמש נפרד ל-API של FilterPhone (חסימות DNS לפי מכשיר).
    המשתמשים הקיימים (של בעל השרת) לא משתנים. אם ל-AdGuard אין משתמשים בכלל — לא מוסיפים,
    כי זה היה מפעיל סיסמה על הממשק ונועל את בעל השרת בחוץ.
"""
from __future__ import annotations

import argparse

import yaml

BYPASS_LIST = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/doh-vpn-proxy-bypass.txt"
CANARY_RULE = "||use-application-dns.net^$dnsrewrite=NXDOMAIN"


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


def main(path: str, api_user: str = "", api_hash: str = "") -> None:
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)

    doc.setdefault("tls", {})["enabled"] = False

    holder = find_key(doc, "safe_search")
    if holder is not None and isinstance(holder["safe_search"], dict):
        holder["safe_search"]["enabled"] = True
        # YouTube must pass through normally during testing; do not rewrite its
        # internal API requests to SafeSearch, which can interfere with playback.
        for engine in ("bing", "duckduckgo", "ecosia", "google", "pixabay", "yandex"):
            holder["safe_search"][engine] = True
        holder["safe_search"]["youtube"] = False

    filters = doc.setdefault("filters", []) or []
    if not any(f.get("url") == BYPASS_LIST for f in filters):
        next_id = max([int(f.get("id", 0)) for f in filters] + [1000]) + 1
        filters.append({"enabled": True, "url": BYPASS_LIST, "id": next_id,
                        "name": "HaGeZi — עקיפות DNS/VPN/פרוקסי"})
    doc["filters"] = filters

    rules = doc.setdefault("user_rules", []) or []
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
