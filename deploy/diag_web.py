"""אבחון רמות הגלישה בשרת — לקריאה בלבד, בלי פרטי לקוחות (רק מספרים ושמות אתרים ציבוריים).

מורץ מ-fp-ci-deploy (`web`) בפייתון של השרת:
    sudo /opt/filterphone/venv/bin/python /opt/filterphone/src/deploy/diag_web.py

מה רואים:
  * רשימת האתרים למבוגרים (הקובץ שה-API מוריד): קיים? כמה אתרים? מתי עודכן?
  * פסקי הדין שבמסד וב-Redis (לפי סוג), התור של הבוט, בקשות הבדיקה (לפי מצב).
  * מכשירים לפי רמת גלישה (מספרים בלבד).
  * מדדי הפרוקסי: ניווטים (allow/check/block), חיפושים שנחסמו, תוצאות שהוסתרו.
  * מה הלקוח רואה בכל רמה: אתרים מהרשימות, ואתרים שלא ברשימות דרך בדיקה חיה של הבוט
    (בלי לשמור כלום) — שההורדה והמנוע עובדים מהשרת, ושההחלטה יוצאת נכונה.
"""
from __future__ import annotations

import collections
import json
import os
import sqlite3
import time

import redis

from filterphone.api import webcheck
from filterphone.proxy.web import CHECK_KEY, VERDICT_KEY
from filterphone.settings import get_settings
from filterphone.websites import GROUPS, LEVEL_ORDER, WEB_LEVELS, WebPolicy, group_of, resolve_groups

cfg = get_settings()
r = redis.Redis.from_url(cfg.redis_url, decode_responses=True, socket_timeout=3)


def section(t: str) -> None:
    print(f"\n== {t}")


section("רשימת האתרים למבוגרים")
path = cfg.adult_list_file
if os.path.exists(path):
    n = sum(1 for line in open(path, encoding="utf-8", errors="replace") if line.strip() and line[0] not in "#!")
    print(f"  {path}: {n} אתרים, עודכן לפני {int((time.time() - os.path.getmtime(path)) / 60)} דקות")
else:
    print(f"  {path}: עוד לא הורד (ה-API מוריד בעלייה ופעם ביום)")

section("רשימות מוכנות")
for g in GROUPS:
    print(f"  {g.id:18} {len(g.domains):4} אתרים · {g.title}")

section("פסקי דין")
db = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
rows = db.execute("SELECT verdict, source, count(*) FROM web_sites GROUP BY verdict, source").fetchall()
for v, s, n in sorted(rows, key=lambda x: -x[2]):
    print(f"  {v:18} {s:6} {n}")
print(f"  במסד: {sum(x[2] for x in rows)} · ב-Redis: {r.hlen(VERDICT_KEY)} · בתור של הבוט: {r.llen(CHECK_KEY)}")

section("בקשות 'שליחה לבדיקה'")
for st, n in db.execute("SELECT status, count(*) FROM web_reviews GROUP BY status").fetchall():
    print(f"  {st:10} {n}")

section("מכשירים לפי רמת גלישה")
levels: collections.Counter[str] = collections.Counter()
for (policy,) in db.execute("SELECT policy FROM devices WHERE status = 'active'"):
    try:
        levels[json.loads(policy or "{}").get("web_level", "")] += 1
    except ValueError:
        levels["?"] += 1
for lv, n in levels.most_common():
    print(f"  {WEB_LEVELS.get(lv, {}).get('title', lv):18} {n}")

section("מדדי הפרוקסי")
m = r.hgetall("fp:metrics:proxy")
for k in sorted(m):
    if any(x in k for x in ("web_pages", "search_total", "serp_results")):
        print(f"  {k} = {m[k]}")
if not any(x in k for k in m for x in ("web_pages", "search_total", "serp")):
    print("  (עוד אין — מופיעים אחרי הניווט / החיפוש הראשון)")


class _Svc:
    def __init__(self):
        self.cfg = cfg
        self.redis = r


desk = webcheck.WebDesk(_Svc())
desk.reload_adult_list()
POLICIES = {lv: WebPolicy(level=lv, groups=frozenset(resolve_groups(lv))) for lv in LEVEL_ORDER if lv}
SHORT = {"allow": "פתוח", "block": "חסום", "check": "בדיקה"}


def row(host: str, verdict: str | None, source: str, took: float, keep: dict) -> None:
    """מה הלקוח רואה בכל רמה — אותה החלטה שהפרוקסי מקבל (WebPolicy.decide)."""
    adult = desk.is_adult(host)
    cells = [f"{WEB_LEVELS[lv]['title']}: {SHORT[POLICIES[lv].decide(host, 'adult' if adult else verdict)]}"
             for lv in POLICIES]
    cells.append(f"{WEB_LEVELS['']['title']}: {'חסום' if adult else 'פתוח'}")
    extra = f"  {json.dumps(keep, ensure_ascii=False)}" if keep else ""
    print(f"  {host:18} {source:22} {took:4.1f}s  " + " · ".join(cells) + extra)


section("מה הלקוח רואה — אתרים מהרשימות (בלי בוט)")
for host in ("hebcal.com", "ilovepdf.com", "ksp.co.il", "kikar.co.il", "ynet.co.il", "aliexpress.com",
             "facebook.com"):
    g = group_of(host)
    known = None if g else desk._known_verdict(host)
    row(host, known, f"רשימה: {g}" if g else f"פסק דין: {known or '—'}", 0.0, {})

section("מה הלקוח רואה — אתרים שלא ברשימות (בדיקה חיה של הבוט, לא נשמר)")
for host in ("example.com", "python.org", "w3.org", "gnu.org", "debian.org"):
    if group_of(host):
        print(f"  {host:18} ברשימה {group_of(host)} — מדלגים")
        continue
    t0 = time.monotonic()
    try:
        verdict, detail = desk.analyze(host)
    except Exception as e:      # האבחון לא נופל בגלל אתר אחד
        verdict, detail = "error", {"error": f"{type(e).__name__}: {e}"[:120]}
    keep = {k: detail[k] for k in ("images_checked", "women_images", "news", "cart_words", "error", "source",
                                   "redirect") if k in detail}
    row(host, verdict, f"בוט: {verdict}", time.monotonic() - t0, keep)
