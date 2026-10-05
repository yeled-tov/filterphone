"""דף "האתר חסום" — מוצג בדפדפן כשהמנהל חסם אתר / שירות למכשיר הזה."""
from __future__ import annotations

import html

_PAGE = """<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>האתר חסום</title>
<style>
:root{{--bg:#f4f6f8;--card:#fff;--text:#18212b;--muted:#66727f;--accent:#1f6feb;--line:#e2e7ec}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1419;--card:#182029;--text:#e6edf3;--muted:#93a1ae;--accent:#4c8dff;--line:#2a3541}}}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);
font:16px/1.6 system-ui,"Segoe UI","Noto Sans Hebrew",Arial,sans-serif;padding:16px;box-sizing:border-box}}
main{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:28px 24px;max-width:420px;text-align:center}}
svg{{width:64px;height:64px;color:var(--accent)}}
h1{{font-size:22px;margin:12px 0 4px}}
p{{color:var(--muted);margin:6px 0}}
code{{direction:ltr;unicode-bidi:embed;color:var(--text)}}
</style></head><body><main>
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"
 stroke-linejoin="round" aria-hidden="true"><path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"/>
<path d="M9 9l6 6M15 9l-6 6"/></svg>
<h1>האתר חסום</h1>
<p><code>{host}</code></p>
<p>{reason}</p>
<p>לפתיחה — פנו למנהל FilterPhone.</p>
</main></body></html>
"""


def block_page(host: str, reason: str = "החסימה הופעלה עבור המכשיר הזה.") -> bytes:
    return _PAGE.format(host=html.escape(host), reason=html.escape(reason)).encode("utf-8")
