"""עדכון הגדרות AdGuard Home (כש-AdGuard כבוי). בטוח להרצה חוזרת.

  * TLS כבוי — פורט 443 עובר ל-Caddy. הטלפונים משתמשים ב-DNS רגיל בתוך המנהרה.
  * חיפוש בטוח: Google, ‏YouTube, ‏Bing ועוד — מאולץ ברמת ה-DNS.
  * רשימת חסימה לשירותי DNS מוצפן / VPN / פרוקסי — כדי שאפליקציות לא יעקפו.
  * use-application-dns.net → NXDOMAIN: מסמן ל-Firefox לא להפעיל DNS מוצפן.
"""
from __future__ import annotations

import sys

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


def main(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)

    doc.setdefault("tls", {})["enabled"] = False

    holder = find_key(doc, "safe_search")
    if holder is not None and isinstance(holder["safe_search"], dict):
        holder["safe_search"]["enabled"] = True
        for engine in ("bing", "duckduckgo", "ecosia", "google", "pixabay", "yandex", "youtube"):
            holder["safe_search"][engine] = True

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

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
    print("  AdGuard עודכן")


if __name__ == "__main__":
    main(sys.argv[1])
