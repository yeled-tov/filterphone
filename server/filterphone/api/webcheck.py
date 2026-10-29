"""בודק האתרים ("הבוט") ובקשות "שליחה לבדיקה" — רמות הגלישה (websites.py).

כשלקוח ברמת "נקי" נכנס לאתר שלא ברשימות, הפרוקסי מציג "בודקים את האתר" ודוחף אותו לתור. הבוט:
  1. בודק ברשימות: הקטלוג (רשתות, וידאו, חדשות…) ורשימת האתרים למבוגרים (HaGeZi NSFW).
  2. מוריד את דף הבית (כמו טלפון — אותה בקשה, דרך המורד המוגן מ-SSRF) ומנתח: מילים מפורשות, סימני
     חדשות / חנות / וידאו.
  3. בודק במנוע הסינון עד web_check_images תמונות מהדף (ברמה המחמירה): תמונה של אישה = "יש תמונות".
  4. פסק דין: adult / news / video / shopping_general / shopping_clean / images / auto_clean / error.
פסק הדין נשמר במסד (web_sites) וב-Redis — והפרוקסי פותח או חוסם לפי הרמה של כל לקוח.

בקשת בדיקה מדף החסימה מגיעה מהפרוקסי (stream). אם הבוט (בבדיקה חוזרת) מוצא שהאתר מתאים לרמה של
הלקוח — הבקשה מאושרת לבד. אחרת היא מחכה למנהל בממשק: לאשר לכולם (פסק דין אנושי לאתר), רק למכשיר
הזה (allow_hosts), או לדחות.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .. import protocol, websites
from ..proxy import serp
from ..proxy.web import CHANGES_KEY, CHECK_KEY, REVIEWS_KEY, VERDICT_KEY, load_domain_list
from ..websites import WebPolicy, group_of, lookup, no_auto, norm_host
from . import db
from .catalog import OPTIONS, DeviceSettings, clean_host, resolve_keywords
from .fetch import FetchError, fetch, fetch_image, fetch_page

log = logging.getLogger("filterphone.webcheck")

REVIEWS_LAST_KEY = "fp:web:reviews:last"
AUTO_TTL_S = 30 * 86400         # פסק דין של הבוט נבדק שוב אחרי חודש
ERROR_RETRY_S = 600             # אתר שלא הצלחנו להוריד — מנסים שוב אחרי 10 דקות
LIST_REFRESH_S = 86400
MAX_IMAGE_BYTES = 4_000_000
MIN_IMAGE_BYTES = 2_500         # אייקונים ופיקסלים — לא נבדקים
THIN_TEXT = 200                 # דף שכמעט אין בו טקסט (נבנה ב-JavaScript) — מסומן לבדיקה של המנהל

# קטגוריות בקטלוג → פסק דין (אתר שמופיע באחת מהן לא נפתח ברמות הרשימה הלבנה)
_CATEGORY_VERDICT = {"social": "social", "video": "video", "ai": "ai", "games": "games", "dating": "dating",
                     "news": "news", "shopping": "shopping_general", "messaging": "social", "protection": "proxy"}
CATALOG_VERDICT: dict[str, str] = {}
for _o in OPTIONS:
    v = _CATEGORY_VERDICT.get(_o.category)
    if v:
        for _h in _o.hosts:
            if "*" not in _h:
                CATALOG_VERDICT.setdefault(_h.lower(), v)

_NEWS_WORDS = re.compile(r"חדשות|מבזק|עיתון|מגזין|\bnews\b|newspaper|magazine|breaking", re.IGNORECASE)
_CART_WORDS = re.compile(r"הוסיפו לסל|הוספה לסל|הוסף לסל|הוספה לעגלה|הוסף לעגלה|לסל הקניות|עגלת קניות|סל קניות|"
                         r"add to (?:cart|bag|basket)|shopping (?:cart|bag)|\bcheckout\b", re.IGNORECASE)
_VIDEO_SRC = re.compile(r"youtube\.com/embed|youtube-nocookie\.com|player\.vimeo\.com|dailymotion\.com/embed",
                        re.IGNORECASE)
_SKIP_IMAGE = re.compile(r"(?:^|[/_.-])(?:logo|icon|sprite|favicon|pixel|spacer|blank|loader|loading|arrow|"
                         r"flag|badge|button|1x1)(?:[/_.-]|\d|$)|\.svg(?:$|\?)", re.IGNORECASE)


@dataclass
class PageInfo:
    title: str = ""
    description: str = ""
    site_name: str = ""
    og_type: str = ""
    text: str = ""
    images: list[str] = field(default_factory=list)
    articles: int = 0
    published: int = 0
    videos: int = 0


class _PageParser(HTMLParser):
    def __init__(self, base: str, max_images: int):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.max_images = max_images
        self.info = PageInfo()
        self._skip = 0
        self._in_title = False
        self._text: list[str] = []
        self._text_len = 0
        self._seen: set[str] = set()

    def _image(self, src: str, attrs: dict[str, str] | None = None) -> None:
        src = (src or "").strip().split(" ")[0]
        if not src or src.startswith("data:") or len(self.info.images) >= self.max_images:
            return
        if attrs:
            for k in ("width", "height"):
                v = attrs.get(k, "")
                if v.isdigit() and int(v) < 64:
                    return
        url = urljoin(self.base, src)
        if not url.startswith(("http://", "https://")) or _SKIP_IMAGE.search(urlsplit(url).path):
            return
        if url not in self._seen:
            self._seen.add(url)
            self.info.images.append(url)

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag in ("script", "style", "noscript", "template"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            content = a.get("content", "")
            if name in ("description", "og:description") and not self.info.description:
                self.info.description = content[:500]
            elif name == "og:site_name":
                self.info.site_name = content[:200]
            elif name == "og:type":
                self.info.og_type = content.lower()[:40]
            elif name in ("og:image", "twitter:image"):
                self._image(content)
            elif name == "article:published_time":
                self.info.published += 1
        elif tag == "img":
            self._image(a.get("src") or a.get("data-src") or a.get("data-lazy-src") or a.get("data-original")
                        or (a.get("srcset") or a.get("data-srcset") or "").split(",")[0], a)
        elif tag == "source" and (a.get("srcset") or "") and "image" in (a.get("type") or "image"):
            self._image(a["srcset"].split(",")[0])
        elif tag == "article":
            self.info.articles += 1
        elif tag == "video":
            self.info.videos += 1
        elif tag == "iframe" and _VIDEO_SRC.search(a.get("src", "") or a.get("data-src", "")):
            self.info.videos += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.info.title += data.strip()
            return
        d = data.strip()
        if d and self._text_len < 200_000:
            self._text.append(d)
            self._text_len += len(d) + 1

    def result(self) -> PageInfo:
        self.info.text = " ".join(self._text)
        return self.info


def parse_page(html_text: str, base_url: str, max_images: int = 12) -> PageInfo:
    p = _PageParser(base_url, max_images)
    try:
        p.feed(html_text)
        p.close()
    except Exception as e:          # HTML שבור — מה שהספקנו לקרוא
        log.debug("page parse stopped: %s", e)
    return p.result()


def classify_page(info: PageInfo, terms: serp.TermMatcher, women: int, adult_images: int) -> tuple[str, dict]:
    """פסק הדין לפי מה שנמצא בדף (טהור). → (פסק דין, סימנים)."""
    head = " ".join((info.title, info.description, info.site_name))
    explicit_head = terms.matches(head)
    explicit_hits = terms.count(info.text)
    news = bool(_NEWS_WORDS.search(" ".join((info.title, info.site_name)))) or info.articles >= 10 \
        or info.published >= 3
    cart = len(_CART_WORDS.findall(info.text))
    signals = {"title": info.title[:120], "explicit_head": explicit_head, "explicit_hits": explicit_hits,
               "news": news, "articles": info.articles, "cart_words": cart, "videos": info.videos,
               "women_images": women, "adult_images": adult_images, "images_found": len(info.images),
               "thin": len(info.text) < THIN_TEXT}
    if explicit_head or explicit_hits >= 3 or adult_images:
        return "adult", signals
    if news:
        return "news", signals
    if info.videos >= 3:
        return "video", signals
    if cart:
        return ("shopping_general" if women else "shopping_clean"), signals
    if women:
        return "images", signals
    return "auto_clean", signals


class WebDesk:
    def __init__(self, svc, classify: Callable[[bytes], dict] | None = None,
                 page_fetcher: Callable[[str], tuple[str, str]] = fetch_page,
                 image_fetcher: Callable[[str], tuple[bytes, str]] | None = None):
        self.svc = svc
        self.cfg = svc.cfg
        self.redis = svc.redis
        self._classify = classify or self._engine_classify
        self._fetch_page = page_fetcher
        self._fetch_image = image_fetcher or (lambda url: fetch_image(url, MAX_IMAGE_BYTES, 6.0, 8.0))
        self.terms = serp.TermMatcher(resolve_keywords({})["terms"])
        self.adult: frozenset[str] = frozenset()
        self._adult_mtime = -1.0
        self._busy = threading.BoundedSemaphore(max(1, self.cfg.web_check_workers))

    # ------------------------------------------------------------ פסקי דין

    def _publish(self, host: str, verdict: str) -> None:
        try:
            with self.redis.pipeline() as p:
                if verdict:
                    p.hset(VERDICT_KEY, host, verdict)
                else:
                    p.hdel(VERDICT_KEY, host)
                p.xadd(CHANGES_KEY, {"host": host, "verdict": verdict}, maxlen=20000, approximate=True)
                p.execute()
        except Exception as e:
            log.warning("web verdict not published for %s: %s", host, e)

    def set_verdict(self, host: str, verdict: str, source: str = "human", by: str = "",
                    detail: dict | None = None) -> dict:
        h = clean_host(host)
        if not h:
            raise ValueError("כתובת אתר לא תקינה")
        h = norm_host(h)
        if verdict not in websites.VERDICTS:
            raise ValueError("פסק דין לא מוכר")
        with self.svc.db() as c, db.transaction(c):
            c.execute("INSERT INTO web_sites (host, verdict, source, detail, checked_at, updated_by)"
                      " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(host) DO UPDATE SET verdict = excluded.verdict,"
                      " source = excluded.source, detail = excluded.detail, checked_at = excluded.checked_at,"
                      " updated_by = excluded.updated_by",
                      (h, verdict, source, json.dumps(detail or {}, ensure_ascii=False), db.now(), by[:60]))
            if source == "human":
                self.svc.audit(by or "admin", "web_verdict", h, verdict, c)
        self._publish(h, verdict)
        return self.site(h)

    def delete_verdict(self, host: str, by: str = "") -> None:
        h = norm_host(clean_host(host) or "")
        with self.svc.db() as c, db.transaction(c):
            c.execute("DELETE FROM web_sites WHERE host = ?", (h,))
            self.svc.audit(by or "admin", "web_verdict", h, "deleted", c)
        self._publish(h, "")

    def site(self, host: str) -> dict:
        with self.svc.db() as c:
            r = c.execute("SELECT * FROM web_sites WHERE host = ?", (norm_host(host),)).fetchone()
        return _site_row(r) if r else {}

    def sites(self, q: str = "", verdict: str = "", source: str = "", limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM web_sites WHERE 1=1", []
        if q:
            sql += " AND host LIKE ?"
            args.append(f"%{q.strip().lower()}%")
        if verdict:
            sql += " AND verdict = ?"
            args.append(verdict)
        if source:
            sql += " AND source = ?"
            args.append(source)
        sql += " ORDER BY checked_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 1000)))
        with self.svc.db() as c:
            return [_site_row(r) for r in c.execute(sql, args).fetchall()]

    def sync_redis(self) -> int:
        """כל פסקי הדין מהמסד ל-Redis (בעלייה — Redis הוא רק עותק)."""
        with self.svc.db() as c:
            rows = c.execute("SELECT host, verdict FROM web_sites").fetchall()
        mapping = {r["host"]: r["verdict"] for r in rows}
        with self.redis.pipeline() as p:
            p.delete(VERDICT_KEY)
            items = list(mapping.items())
            for i in range(0, len(items), 1000):
                p.hset(VERDICT_KEY, mapping=dict(items[i:i + 1000]))
            p.execute()
        return len(mapping)

    # ------------------------------------------------------------ הבוט

    def _engine_classify(self, data: bytes) -> dict:
        header, _ = protocol.call_sync(self.cfg.engine_socket, {"op": "classify", "level": 1, "budget_ms": 8000},
                                       data, timeout=12)
        return header

    def _known_verdict(self, host: str) -> str | None:
        if self.is_adult(host):
            return "adult"
        return lookup(CATALOG_VERDICT, host)

    def is_adult(self, host: str) -> bool:
        self.reload_adult_list()
        parts = host.split(".")
        return any(".".join(parts[i:]) in self.adult for i in range(len(parts) - 1))

    def analyze(self, host: str) -> tuple[str, dict]:
        """בודק אתר (חוסם — רשת + מנוע). → (פסק דין, פרטים)."""
        host = norm_host(host)
        known = self._known_verdict(host)
        if known:
            return known, {"source": "list"}
        try:
            html_text, final = self._fetch_page(f"https://{host}/")
        except FetchError as e:
            try:
                html_text, final = self._fetch_page(f"http://{host}/")
            except FetchError:
                return "error", {"error": str(e)[:200]}
        final_host = norm_host(urlsplit(final).hostname or host)
        if final_host != host:
            # מפנה לאתר מוכר (רשת חברתית, אתר חדשות שברשימות…) — פסק הדין שלו
            known = self._known_verdict(final_host) or group_of(final_host)
            if known:
                return known, {"redirect": final_host}
        info = parse_page(html_text, final, self.cfg.web_check_images)
        women = adult_images = checked = 0

        def one(url: str) -> str:
            try:
                data, _ = self._fetch_image(url)
            except FetchError:
                return "skip"
            if len(data) < MIN_IMAGE_BYTES:
                return "skip"
            try:
                header = self._classify(data)
            except (OSError, protocol.ProtocolError, ValueError) as e:
                log.debug("engine unavailable for site check: %s", e)
                return "skip"
            action = str(header.get("action", "pass"))
            reason = str(header.get("reason", ""))
            if action == "pass":
                return "pass"
            return "adult" if ("nsfw" in reason or "explicit" in reason) else "woman"

        if info.images:
            with ThreadPoolExecutor(max_workers=4) as ex:
                for r in ex.map(one, info.images):
                    if r != "skip":
                        checked += 1
                    women += r == "woman"
                    adult_images += r == "adult"
        verdict, signals = classify_page(info, self.terms, women, adult_images)
        signals["images_checked"] = checked
        if final_host != host:
            signals["redirect"] = final_host
        return verdict, signals

    def check(self, host: str, force: bool = False) -> dict:
        """בודק ושומר (אם צריך). → השורה של האתר."""
        h = norm_host(clean_host(host) or "")
        if not h or no_auto(h) or group_of(h):
            return self.site(h) if h else {}
        cur = self.site(h)
        if cur and not force:
            age = time.time() - cur["checked_at"]
            if cur["source"] == "human" or (cur["verdict"] != "error" and age < AUTO_TTL_S) \
                    or (cur["verdict"] == "error" and age < ERROR_RETRY_S):
                self._publish(h, cur["verdict"])        # הפרוקסי אולי פספס — מפרסמים שוב
                return cur
        if cur and cur["source"] == "human" and force:
            return cur                                   # החלטה של אדם — הבוט לא דורס
        with self._busy:
            verdict, detail = self.analyze(h)
        log.info("site check %s → %s", h, verdict)
        return self.set_verdict(h, verdict, "auto", "bot", detail)

    def pop_check(self, timeout: float = 1.0) -> str | None:
        item = self.redis.brpop(CHECK_KEY, timeout=timeout)
        return item[1] if item else None

    def check_safe(self, host: str) -> None:
        try:
            self.check(host)
        except Exception as e:
            log.warning("site check failed for %s: %s", host, e)

    # ------------------------------------------------------------ רשימת האתרים למבוגרים

    def reload_adult_list(self) -> None:
        path = self.cfg.adult_list_file
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return
        if mtime != self._adult_mtime:
            self.adult = load_domain_list(path)
            self._adult_mtime = mtime

    def refresh_adult_list(self, force: bool = False) -> bool:
        """מוריד את הרשימה (פעם ביום). הפרוקסי טוען את הקובץ כשהוא משתנה."""
        path = self.cfg.adult_list_file
        try:
            if not force and time.time() - os.path.getmtime(path) < LIST_REFRESH_S:
                return False
        except OSError:
            pass
        data, _, _ = fetch(self.cfg.web_adult_list_url, 40_000_000, 30.0, 180.0, "text/plain,*/*", "הרשימה גדולה מדי")
        if data.count(b"\n") < 1000:
            raise FetchError("הרשימה שהתקבלה קצרה מדי")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        self.reload_adult_list()
        log.info("adult list refreshed: %d domains", len(self.adult))
        return True

    # ------------------------------------------------------------ בקשות בדיקה

    def _device_policy(self, row: sqlite3.Row) -> WebPolicy:
        s = DeviceSettings.load(row["policy"])
        return WebPolicy(level=s.web_level, groups=frozenset(s.web_groups), allow=tuple(s.allow_hosts))

    def ingest_reviews(self) -> int:
        """בקשות חדשות מהפרוקסי → מסד. בודקים כל אתר; מה שמתאים לרמת הלקוח — מאושר לבד."""
        last = self.redis.get(REVIEWS_LAST_KEY) or "0-0"
        res = self.redis.xread({REVIEWS_KEY: last}, count=100)
        n = 0
        for _stream, entries in res or ():
            for entry_id, f in entries:
                self.redis.set(REVIEWS_LAST_KEY, entry_id)
                try:
                    if self._ingest_one(f):
                        n += 1
                except Exception as e:
                    log.warning("review request failed: %s", e)
        return n

    def _ingest_one(self, f: dict) -> bool:
        host = norm_host(clean_host(f.get("host", "")) or "")
        if not host:
            return False
        with self.svc.db() as c:
            dev = c.execute("SELECT * FROM devices WHERE vpn_ip = ? AND status = 'active'", (f.get("ip", ""),)).fetchone()
        if dev is None:
            return False
        with self.svc.db() as c, db.transaction(c):
            if c.execute("SELECT 1 FROM web_reviews WHERE device_id = ? AND host = ? AND status = 'pending'",
                         (dev["id"], host)).fetchone():
                return False
            rid = c.execute("INSERT INTO web_reviews (device_id, host, url, note, created_at) VALUES (?, ?, ?, ?, ?)",
                            (dev["id"], host, str(f.get("url", ""))[:500], str(f.get("note", ""))[:300],
                             db.now())).lastrowid
            self.svc.log_device(c, dev["id"], "web_review", host)
        site = self.check(host, force=self.site(host).get("verdict") == "error")
        verdict = site.get("verdict", "")
        allowed = bool(verdict) and self._device_policy(dev).decide(host, verdict) == "allow"
        with self.svc.db() as c, db.transaction(c):
            c.execute("UPDATE web_reviews SET auto_verdict = ?, status = ?, decision = ?, decided_at = ?,"
                      " decided_by = ? WHERE id = ?",
                      (verdict, "approved" if allowed else "pending", "auto" if allowed else "",
                       db.now() if allowed else None, "bot" if allowed else "", rid))
        return True

    def reviews(self, status: str = "pending", limit: int = 200) -> list[dict]:
        sql = ("SELECT r.*, d.name AS device_name, cu.name AS customer FROM web_reviews r"
               " LEFT JOIN devices d ON d.id = r.device_id LEFT JOIN customers cu ON cu.id = d.customer_id")
        args: list = []
        if status:
            sql += " WHERE r.status = ?"
            args.append(status)
        sql += " ORDER BY r.created_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 500)))
        with self.svc.db() as c:
            rows = c.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["site"] = self.site(r["host"])
            out.append(d)
        return out

    def pending_count(self) -> int:
        with self.svc.db() as c:
            return c.execute("SELECT count(*) FROM web_reviews WHERE status = 'pending'").fetchone()[0]

    def decide_review(self, review_id: int, action: str, verdict: str = "", by: str = "admin") -> dict:
        """action: global (פסק דין לאתר לכולם, [verdict]) / device (רק למכשיר הזה) / reject."""
        with self.svc.db() as c:
            r = c.execute("SELECT * FROM web_reviews WHERE id = ?", (review_id,)).fetchone()
        if r is None:
            raise LookupError("בקשה לא נמצאה")
        host = r["host"]
        if action == "global":
            self.set_verdict(host, verdict, "human", by)
            # כל הבקשות הממתינות לאתר הזה (מכל המכשירים) — לפי הרמה של כל אחד
            with self.svc.db() as c:
                pend = c.execute("SELECT r.id, d.* FROM web_reviews r JOIN devices d ON d.id = r.device_id"
                                 " WHERE r.host = ? AND r.status = 'pending'", (host,)).fetchall()
            for p in pend:
                ok = self._device_policy(p).decide(host, verdict) == "allow"
                self._close(p[0], "approved" if ok else "rejected", f"global:{verdict}", by)
        elif action == "device":
            if r["device_id"] is None:
                raise LookupError("המכשיר כבר לא קיים")
            with self.svc.db() as c:
                dev = c.execute("SELECT policy FROM devices WHERE id = ?", (r["device_id"],)).fetchone()
            allow = DeviceSettings.load(dev["policy"]).allow_hosts if dev else []
            self.svc.update_device(r["device_id"], {"allow_hosts": sorted({*allow, host})}, by)
            self._close(review_id, "approved", "device", by)
        elif action == "reject":
            self._close(review_id, "rejected", "reject", by)
        else:
            raise ValueError("פעולה לא מוכרת")
        with self.svc.db() as c:
            return dict(c.execute("SELECT * FROM web_reviews WHERE id = ?", (review_id,)).fetchone())

    def _close(self, review_id: int, status: str, decision: str, by: str) -> None:
        with self.svc.db() as c, db.transaction(c):
            c.execute("UPDATE web_reviews SET status = ?, decision = ?, decided_at = ?, decided_by = ? WHERE id = ?",
                      (status, decision, db.now(), by[:60], review_id))


def _site_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    try:
        d["detail"] = json.loads(d.get("detail") or "{}")
    except ValueError:
        d["detail"] = {}
    d["title"] = websites.VERDICT_TITLES.get(d["verdict"], d["verdict"])
    return d
