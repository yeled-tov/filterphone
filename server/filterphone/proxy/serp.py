"""סינון דפי תוצאות חיפוש (Google / Bing) — מה שלא מותר ללקוח לא מופיע בכלל.

שני חלקים:
  * מילת חיפוש מפורשת (פורנו וכו', הרשימה של חסימת מילות החיפוש) — אפס תוצאות: במקום דף התוצאות
    מוצג דף "לא נמצאו תוצאות", והצעות ההשלמה האוטומטית לא חוזרות.
  * תוצאה שמובילה לאתר חסום ללקוח (רשת חברתית שנחסמה, אתר למבוגרים, אתר מחוץ לרמת הגלישה שלו)
    — נמחקת מהדף לפני שהוא מגיע לטלפון.

דף התוצאות של Google משתנה כל הזמן, לכן לא נשענים על מבנה קבוע: מפענחים את ה-HTML לעץ עם מיקומים
(html.parser של פייתון — בלי ספריות), מוצאים כל קישור החוצה (כולל הפניות ‎/url?q=‎ של Google ו-‎/ck/a‎
של Bing), ומוחקים את "בלוק התוצאה" שלו: המיכל הגבוה ביותר שכל הקישורים החוצה בתוכו מובילים לאתרים
חסומים — עדיף מיכל שמסומן כתוצאה (מחלקות מוכרות), ובלי לעלות מעל אזור התוצאות / טופס החיפוש.
כל תקלה בפענוח — הדף עובר כמו שהוא (הסינון ברשת עדיין חוסם את האתר עצמו בלחיצה).
"""
from __future__ import annotations

import base64
import re
import unicodedata
from collections.abc import Callable, Iterable
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

# ---------------------------------------------------------------- מנועי חיפוש

_GOOGLE = re.compile(r"^(?:www\.)?google\.(?:com|co\.il|[a-z]{2,3}|com?\.[a-z]{2})$")
_BING = re.compile(r"^(?:www\.|m\.)?bing\.com$")
_DDG = re.compile(r"^(?:html\.|lite\.)?duckduckgo\.com$")
_YAHOO = re.compile(r"^(?:[a-z]+\.)?search\.yahoo\.com$")
_YOUTUBE = re.compile(r"^(?:www\.|m\.)?youtube\.com$")
_YANDEX = re.compile(r"^(?:www\.)?yandex\.[a-z.]+$")


def is_google(host: str) -> bool:
    return bool(_GOOGLE.match(host.lower()))


def search_query(host: str, path: str) -> tuple[str, str, bool] | None:
    """(מנוע, מילות החיפוש, הצעות?) אם זו בקשת חיפוש; אחרת None. הצעות = השלמה אוטומטית."""
    host = host.lower().rstrip(".")
    base, _, query = path.partition("?")
    if not query:
        return None
    q = parse_qs(query, keep_blank_values=False)

    def first(*keys: str) -> str:
        for k in keys:
            if q.get(k):
                return q[k][0]
        return ""

    if _GOOGLE.match(host):
        if base.startswith("/complete/search"):
            return "google", first("q"), True
        if base in ("/search", "/", "/webhp", "/m/search") or base.startswith("/search"):
            return ("google", first("q"), False) if first("q") else None
        return None
    if _BING.match(host):
        if base.lower().startswith(("/as/suggestions", "/osjson.aspx")):
            return "bing", first("qry", "query", "q"), True
        if base.lower().startswith("/search") or base.lower().startswith("/images/search"):
            return ("bing", first("q"), False) if first("q") else None
        return None
    if _DDG.match(host):
        if base.startswith("/ac"):
            return "duckduckgo", first("q"), True
        return ("duckduckgo", first("q"), False) if first("q") else None
    if _YAHOO.match(host):
        return ("yahoo", first("p", "q"), False) if first("p", "q") else None
    if _YOUTUBE.match(host) and base.startswith("/results"):
        return ("youtube", first("search_query"), False) if first("search_query") else None
    if _YANDEX.match(host) and base.startswith("/search"):
        return ("yandex", first("text"), False) if first("text") else None
    return None


# ---------------------------------------------------------------- מילים מפורשות

_MARKS = re.compile(r"[֑-ׇ]")          # ניקוד וטעמים
_HEB_PREFIX = "והבלמשכ"


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = _MARKS.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


class TermMatcher:
    """התאמה של מילות חיפוש לרשימת מונחים: לטינית — מילה שלמה (Sussex לא נתפס בגלל sex);
    עברית — גם עם אותיות שימוש בתחילת המילה (הפורנו, ובפורנו); שפות בלי רווחים (סינית, יפנית) — תת-מחרוזת."""

    def __init__(self, terms: Iterable[str]):
        latin, other, cjk = [], [], []
        for t in {norm(x) for x in terms if x and x.strip()}:
            if re.search(r"[぀-ヿ一-鿿가-힯]", t):
                cjk.append(t)
            elif re.search(r"[֐-׿]", t):
                other.append(rf"(?<!\w)[{_HEB_PREFIX}]{{0,2}}{re.escape(t)}(?!\w)")
            else:
                latin.append(rf"(?<!\w){re.escape(t)}(?!\w)")
        parts = latin + other + [re.escape(t) for t in cjk]
        self._re = re.compile("|".join(parts)) if parts else None

    def matches(self, text: str) -> bool:
        return bool(self._re and self._re.search(norm(text)))

    def count(self, text: str) -> int:
        return sum(1 for _ in self._re.finditer(norm(text))) if self._re else 0


# ---------------------------------------------------------------- דפים

NO_RESULTS_PAGE = """<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>לא נמצאו תוצאות</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f6f8;color:#18212b;
font:16px/1.6 system-ui,Arial,sans-serif;padding:16px;box-sizing:border-box}
main{background:#fff;border:1px solid #e2e7ec;border-radius:18px;padding:28px 24px;max-width:420px;text-align:center}
@media (prefers-color-scheme:dark){body{background:#0f1419;color:#e6edf3}main{background:#182029;border-color:#2a3541}}
p{color:#66727f}</style></head><body><main>
<h1>לא נמצאו תוצאות</h1><p>החיפוש הזה לא מתאים לסינון של הטלפון.</p>
<p><a href="/">לחיפוש חדש</a></p></main></body></html>
"""


def no_results_page() -> bytes:
    return NO_RESULTS_PAGE.encode("utf-8")


# ---------------------------------------------------------------- עץ HTML עם מיקומים

_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
                   "track", "wbr", "keygen", "command"})
# מיכלי תוצאה מוכרים (Google נייד ומחשב, Bing). רק רמז — יש גם גיבוי כללי.
RESULT_CLASSES = frozenset({"MjjYud", "g", "Gx5Zad", "xpd", "mnr-c", "tF2Cxc", "hlcw0c", "ULSxyf", "b_algo",
                            "b_ans", "kp-blk", "WlydOe", "SoaBEf", "Ww4FFb", "vt6azd"})
_STOP_IDS = frozenset({"search", "rso", "center_col", "main", "b_results", "b_content", "botstuff", "taw",
                       "appbar", "cnt", "rcnt", "b_context"})
_STOP_TAGS = frozenset({"#root", "html", "body", "main", "form", "header", "nav"})
_INPUTS = frozenset({"input", "form", "textarea", "select"})
_GENERIC_MAX_LEVELS = 3
MAX_BLOCK_SHARE = 0.3           # בלוק תוצאה אחד לא אמור להיות יותר מזה מהדף


class _Node:
    __slots__ = ("tag", "start", "end", "parent", "attrs", "hosts", "inputs")

    def __init__(self, tag: str, start: int, parent: _Node | None, attrs: dict[str, str]):
        self.tag = tag
        self.start = start
        self.end = -1
        self.parent = parent
        self.attrs = attrs
        self.hosts: set[str] = set()
        self.inputs = False

    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    @property
    def stop(self) -> bool:
        return self.tag in _STOP_TAGS or self.attrs.get("id", "") in _STOP_IDS or self.attrs.get("role") == "main"


class _Tree(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.text = text
        self._lines = [0] + [m.end() for m in re.finditer("\n", text)]
        self.root = _Node("#root", 0, None, {})
        self.root.end = len(text)
        self.stack = [self.root]
        self.nodes: list[_Node] = []
        self.anchors: list[_Node] = []

    def _pos(self) -> int:
        line, col = self.getpos()
        return self._lines[line - 1] + col

    def _add(self, tag: str, attrs) -> _Node:
        start = self._pos()
        node = _Node(tag, start, self.stack[-1], {k: v or "" for k, v in attrs})
        self.nodes.append(node)
        if tag == "a" and node.attrs.get("href"):
            self.anchors.append(node)
        if tag in _INPUTS:
            n: _Node | None = node
            while n is not None and not n.inputs:
                n.inputs = True
                n = n.parent
        return node

    def handle_starttag(self, tag, attrs):
        node = self._add(tag, attrs)
        if tag in _VOID:
            node.end = node.start + len(self.get_starttag_text() or "")
        else:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = self._add(tag, attrs)
        node.end = node.start + len(self.get_starttag_text() or "")

    def handle_endtag(self, tag):
        pos = self._pos()
        gt = self.text.find(">", pos)
        end = gt + 1 if gt != -1 else len(self.text)
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                for n in self.stack[i + 1:]:
                    n.end = pos                 # נסגר בלי תגית סוגרת
                self.stack[i].end = end
                del self.stack[i:]
                return
        # תגית סוגרת בלי פתיחה — מתעלמים

    def finish(self) -> None:
        self.close()
        for n in self.stack[1:]:
            n.end = len(self.text)
        del self.stack[1:]


# ---------------------------------------------------------------- קישורים

_INTERNAL = re.compile(r"(?:^|\.)(?:google\.[a-z.]+|gstatic\.com|googleapis\.com|googleusercontent\.com|"
                       r"bing\.com|bing\.net|microsoft\.com|msn\.com|live\.com|duckduckgo\.com)$")


def _bing_target(u: str) -> str:
    if not u.startswith("a1"):
        return ""
    data = u[2:]
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    except (ValueError, UnicodeError):
        return ""


def link_host(href: str) -> str | None:
    """האתר שאליו הקישור מוביל (מחוץ למנוע החיפוש), או None לקישור פנימי / לא רלוונטי."""
    href = (href or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return None
    try:
        parts = urlsplit(href)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    path = parts.path or ""
    if not host or _INTERNAL.search(host):
        # הפניות של מנוע החיפוש: ‎/url?q=… (Google), ‎/ck/a?…&u=a1… (Bing)
        q = parse_qs(parts.query)
        target = ""
        if path in ("/url", "/interstitial", "/imgres"):
            target = (q.get("q") or q.get("url") or q.get("imgrefurl") or [""])[0]
        elif path.startswith("/ck/a"):
            target = _bing_target((q.get("u") or [""])[0])
        if target and target.startswith(("http://", "https://")):
            t = (urlsplit(target).hostname or "").lower()
            return t if t and not _INTERNAL.search(t) else None
        return None
    if parts.scheme not in ("http", "https", ""):
        return None
    return host


# ---------------------------------------------------------------- מחיקת תוצאות

def _choose(anchor: _Node, bad: Callable[[set[str]], bool]) -> _Node:
    """הבלוק למחיקה עבור קישור חסום: הגבוה ביותר שכל הקישורים החוצה בו חסומים, בלי טפסים, מתחת לאזור
    התוצאות. אם יש בשרשרת מיכל תוצאה מוכר — הגבוה מביניהם; אחרת עולים לכל היותר כמה רמות."""
    chain = [anchor]
    n = anchor.parent
    while n is not None and not n.stop and not n.inputs and bad(n.hosts):
        chain.append(n)
        n = n.parent
    marked = [c for c in chain if c.classes() & RESULT_CLASSES]
    if marked:
        return marked[-1]
    return chain[min(len(chain) - 1, _GENERIC_MAX_LEVELS)]


def filter_results(html_text: str, hidden: Callable[[str], bool], notice: bool = True) -> tuple[str, int]:
    """מוחק מדף התוצאות כל תוצאה שמובילה לאתר ש-[hidden] מחזירה עליו True. → (דף חדש, כמה נמחקו)."""
    tree = _Tree(html_text)
    tree.feed(html_text)
    tree.finish()
    verdict: dict[str, bool] = {}

    def is_hidden(host: str) -> bool:
        if host not in verdict:
            verdict[host] = bool(hidden(host))
        return verdict[host]

    bad_anchors: list[_Node] = []
    for a in tree.anchors:
        host = link_host(a.attrs.get("href", ""))
        if host is None:
            continue
        n: _Node | None = a
        while n is not None:
            n.hosts.add(host)
            n = n.parent
        if is_hidden(host):
            bad_anchors.append(a)
    if not bad_anchors:
        return html_text, 0

    def all_bad(hosts: set[str]) -> bool:
        return all(is_hidden(h) for h in hosts)

    # הגנה: בלוק שגדול מחלק ניכר מהדף כנראה טיפס גבוה מדי (מבנה לא צפוי) — מוחקים רק את הקישור עצמו
    max_span = max(2000, int(len(html_text) * MAX_BLOCK_SHARE))
    chosen = []
    for a in bad_anchors:
        b = _choose(a, all_bad)
        chosen.append(b if b.end - b.start <= max_span else a)
    spans = sorted({(b.start, b.end) for b in chosen if b.end > b.start})
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))      # מקונן — כבר נמחק עם ההורה
        else:
            merged.append((s, e))
    out, last = [], 0
    for s, e in merged:
        out.append(html_text[last:s])
        last = e
    out.append(html_text[last:])
    result = "".join(out)
    removed = len(merged)
    if notice:
        result = _with_notice(result, removed)
    return result, removed


_BODY = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


def _with_notice(text: str, removed: int) -> str:
    m = _BODY.search(text)
    if not m:
        return text
    note = ('<div dir="rtl" style="font:13px/1.5 system-ui,Arial,sans-serif;background:#eef4ff;color:#1f3b73;'
            'padding:6px 12px;text-align:center">FilterPhone: הוסתרו '
            f'{removed} תוצאות שלא מתאימות לסינון שלך</div>')
    return text[:m.end()] + note + text[m.end():]
