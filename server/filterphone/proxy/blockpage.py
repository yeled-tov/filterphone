"""הדפים שהפרוקסי מציג בדפדפן במקום אתר: "האתר חסום" (עם בקשת בדיקה), "בודקים את האתר", "נשלח לבדיקה".

הטופס של "שליחה לבדיקה" נשלח לאותו אתר בנתיב REVIEW_PATH — הפרוקסי (שמיירט את האתר הזה ממילא)
תופס את הבקשה, רושם אותה ומחזיר אישור. כך אין צורך בהתחברות: הפרוקסי יודע מאיזה מכשיר הבקשה הגיעה.
"""
from __future__ import annotations

import html

REVIEW_PATH = "/__filterphone__/review"

_STYLE = """
:root{--bg:#f4f6f8;--card:#fff;--text:#18212b;--muted:#66727f;--accent:#1f6feb;--line:#e2e7ec}
@media (prefers-color-scheme:dark){:root{--bg:#0f1419;--card:#182029;--text:#e6edf3;--muted:#93a1ae;--accent:#4c8dff;--line:#2a3541}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);
font:16px/1.6 system-ui,"Segoe UI","Noto Sans Hebrew",Arial,sans-serif;padding:16px;box-sizing:border-box}
main{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:28px 24px;max-width:420px;
width:100%;box-sizing:border-box;text-align:center}
svg{width:64px;height:64px;color:var(--accent)}
h1{font-size:22px;margin:12px 0 4px}
p{color:var(--muted);margin:6px 0}
code{direction:ltr;unicode-bidi:embed;color:var(--text);word-break:break-all}
textarea{width:100%;box-sizing:border-box;min-height:64px;margin-top:12px;border:1px solid var(--line);
border-radius:10px;padding:8px;font:inherit;background:var(--bg);color:var(--text)}
button,.btn{display:block;width:100%;margin-top:12px;padding:12px;border:0;border-radius:12px;background:var(--accent);
color:#fff;font:inherit;font-weight:600;cursor:pointer;text-decoration:none}
.sec{background:transparent;color:var(--accent);border:1px solid var(--line)}
.spin{width:36px;height:36px;margin:8px auto;border:4px solid var(--line);border-top-color:var(--accent);
border-radius:50%;animation:s 1s linear infinite}@keyframes s{to{transform:rotate(360deg)}}
"""

_SHIELD = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"
 stroke-linejoin="round" aria-hidden="true"><path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"/>{mark}</svg>"""
_X = '<path d="M9 9l6 6M15 9l-6 6"/>'
_CHECK = '<path d="M8.5 12.5l2.5 2.5 4.5-5"/>'


def _page(title: str, body: str, head: str = "") -> bytes:
    return (f'<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">{head}'
            f'<title>{html.escape(title)}</title><style>{_STYLE}</style></head><body><main>{body}</main></body></html>'
            ).encode()


def block_page(host: str, reason: str = "החסימה הופעלה עבור המכשיר הזה.", review: bool = False,
               url: str = "") -> bytes:
    """[review] — עם כפתור "שליחה לבדיקה" (אתר שלא ברמת הגלישה של הלקוח)."""
    form = ""
    if review:
        form = (f'<form method="post" action="{REVIEW_PATH}">'
                f'<input type="hidden" name="url" value="{html.escape(url or "https://" + host + "/")}">'
                '<textarea name="note" maxlength="300" placeholder="למה צריך את האתר? (לא חובה)"></textarea>'
                '<button type="submit">שליחה לבדיקה</button></form>'
                '<p style="font-size:14px">האתר ייבדק (אוטומטית, ואם צריך — גם על ידי אדם). אם הוא מתאים '
                'לסינון שבחרת — הוא ייפתח.</p>')
    else:
        form = "<p>לפתיחה — פנו למנהל FilterPhone.</p>"
    body = (_SHIELD.format(mark=_X) + "<h1>האתר חסום</h1>"
            f"<p><code>{html.escape(host)}</code></p><p>{html.escape(reason)}</p>{form}"
            '<a class="btn sec" href="javascript:history.back()">חזרה</a>')
    return _page("האתר חסום", body)


def checking_page(host: str, refresh_s: int = 3) -> bytes:
    """האתר עוד לא נבדק: הבוט בודק אותו עכשיו; הדף מתרענן לבד עד שיש תשובה."""
    body = (_SHIELD.format(mark=_CHECK) + "<h1>בודקים את האתר…</h1>"
            f"<p><code>{html.escape(host)}</code></p><div class=spin></div>"
            "<p>זו הפעם הראשונה שנכנסים לאתר הזה. בודקים שהוא נקי — בדרך כלל עד כמה שניות. "
            "הדף ייפתח לבד.</p>")
    return _page("בודקים את האתר", body, f'<meta http-equiv="refresh" content="{int(refresh_s)}">')


def review_sent_page(host: str) -> bytes:
    body = (_SHIELD.format(mark=_CHECK) + "<h1>נשלח לבדיקה</h1>"
            f"<p><code>{html.escape(host)}</code></p>"
            "<p>הבקשה התקבלה. אם האתר מתאים לסינון שלך — הוא ייפתח (נסו שוב בעוד כמה דקות).</p>"
            '<a class="btn sec" href="javascript:history.go(-2)">חזרה</a>')
    return _page("נשלח לבדיקה", body)
