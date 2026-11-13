"""אייפון: דפי ההתקנה שנפתחים בספארי — הזנת קוד הפעלה, ואז מדריך צעד-אחר-צעד עם בדיקה חיה.

הדף עצמו (api.filterphone.com) לא עובר בפרוקסי גם כשהמנהרה מחוברת (deploy/firewall.sh — DNAT ישיר לשרת),
ולכן הוא נשאר נגיש לאורך כל ההתקנה, גם לפני שהתעודה אושרה. הבדיקה החיה:
  1. "מחובר" — הבקשה של הדף הגיעה מכתובת ה-VPN של המכשיר (או שיש handshake טרי בשרת).
  2. "התעודה מאושרת" — הדפדפן מצליח לפנות ב-HTTPS לשרת בדיקה שהפרוקסי תמיד פותח ולעולם לא "לומד לעקוף"
     (PROBE_URL, רשום ב-[strict] ב-passthrough.txt). בלי אמון בתעודה הפנייה נכשלת.
"""
from __future__ import annotations

import html

from . import iosprofile

APP_STORE_WIREGUARD = "https://apps.apple.com/app/wireguard/id1441195209"
PROBE_URL = "https://www.filterphone.com/"      # חייב להישאר ב-[strict] (proxy/passthrough.txt)
PROFILE_NAME = "FilterPhone.mobileconfig"

_CSS = """
:root{--bg:#f4f7fb;--card:#fff;--ink:#0d2238;--sub:#4a5f75;--line:#e3eaf2;--blue:#0b78bd;--ok:#15803d;--warn:#b45309}
@media (prefers-color-scheme:dark){:root{--bg:#0f1720;--card:#18222d;--ink:#e6eef5;--sub:#9fb2c3;--line:#263544;--blue:#66b8f0;--ok:#4ade80;--warn:#fbbf24}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,system-ui,Arial,sans-serif;line-height:1.55}
.w{max-width:560px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:24px;margin:8px 0 4px}.lead{color:var(--sub);margin:0 0 18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px 16px 14px;margin:0 0 12px}
.step{display:flex;gap:12px;align-items:flex-start}
.n{flex:none;width:30px;height:30px;border-radius:50%;background:var(--blue);color:#fff;display:grid;place-items:center;font-weight:700}
.step h2{font-size:17px;margin:2px 0 4px}.step p{margin:0 0 8px;color:var(--sub);font-size:15px}
.path{display:inline-block;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:2px 8px;font-size:14px;color:var(--ink)}
a.btn,button{display:inline-block;font:inherit;font-weight:700;font-size:16px;border:0;border-radius:12px;padding:12px 18px;background:var(--blue);color:#fff;text-decoration:none;cursor:pointer}
a.btn.ghost{background:transparent;color:var(--blue);border:1px solid var(--line)}
input{width:100%;font:inherit;font-size:17px;padding:12px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink);margin:4px 0 12px}
label{font-weight:700;font-size:15px}
.err{background:#fee2e2;color:#991b1b;border-radius:12px;padding:10px 12px;margin:0 0 12px}
.note{font-size:14px;color:var(--sub)}
.status{position:sticky;top:8px;z-index:2}
.row{display:flex;gap:8px;align-items:center;margin:4px 0;font-weight:600}
.dot{width:12px;height:12px;border-radius:50%;background:#94a3b8;flex:none}
.dot.ok{background:var(--ok)}.dot.wait{background:var(--warn)}
.done{display:none;font-weight:800;color:var(--ok);font-size:18px;margin-top:6px}
small.en{color:var(--sub);direction:ltr;unicode-bidi:isolate}
"""


def _page(title: str, body: str, script: str = "") -> str:
    return (f'<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex"><title>{html.escape(title)}</title><style>{_CSS}</style></head>'
            f'<body><div class="w">{body}</div>{script}</body></html>')


def start_page(error: str = "", code: str = "", name: str = "") -> str:
    """הזנת קוד ההפעלה (אותו קוד כמו באנדרואיד)."""
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    body = (
        '<h1>FilterPhone לאייפון</h1>'
        '<p class="lead">התקנת הסינון על האייפון: מזינים את קוד ההפעלה, ומקבלים הוראות התקנה צעד אחר צעד.</p>'
        f'{err}<form method="post" class="card">'
        '<label for="code">קוד הפעלה</label>'
        f'<input id="code" name="code" required autocomplete="off" autocapitalize="characters" dir="ltr" '
        f'placeholder="XXXXX-XXXXX" value="{html.escape(code)}">'
        '<label for="name">שם למכשיר</label>'
        f'<input id="name" name="name" maxlength="60" placeholder="האייפון של משה" value="{html.escape(name)}">'
        '<button>המשך</button></form>'
        '<p class="note">את הדף הזה פותחים באייפון עצמו, בדפדפן <b>Safari</b> (לא ב-Chrome ולא מתוך וואטסאפ) — '
        'רק ספארי יודע להתקין פרופיל.</p>'
    )
    return _page("FilterPhone לאייפון", body)


def gone_page(message: str) -> str:
    body = (f'<h1>הקישור לא בתוקף</h1><p class="lead">{html.escape(message)}</p>'
            '<p class="note">קישור ההתקנה תקף לשעתיים. המנהל יכול להפיק קישור חדש מדף המכשיר בדשבורד.</p>')
    return _page("FilterPhone לאייפון", body)


def _step(n: int, title: str, inner: str) -> str:
    return (f'<div class="card step"><div class="n">{n}</div><div><h2>{title}</h2>{inner}</div></div>')


def install_page(token: str, name: str) -> str:
    """מדריך ההתקנה + בדיקה חיה. [token] — הטוקן של הקישור (כבר נבדק)."""
    base = f"/v1/ios/p/{html.escape(token)}"
    steps = "".join([
        _step(1, "מתקינים את WireGuard",
              '<p>אפליקציה חינמית מה-App Store — היא מריצה את החיבור המסונן. רק להתקין, בלי להגדיר בה כלום.</p>'
              f'<a class="btn ghost" href="{APP_STORE_WIREGUARD}">פתיחה ב-App Store</a>'),
        _step(2, "מורידים את הפרופיל",
              '<p>לוחצים על הכפתור. ספארי ישאל אם לאפשר הורדת פרופיל — בוחרים <b>אפשר</b> '
              '<small class="en">(Allow)</small>.</p>'
              f'<a class="btn" href="{base}/{PROFILE_NAME}">הורדת הפרופיל</a>'),
        _step(3, "מתקינים את הפרופיל",
              '<p>פותחים את <span class="path">הגדרות</span> — למעלה מופיע <span class="path">הפרופיל הורד</span> '
              '<small class="en">(Profile Downloaded)</small>. נכנסים, לוחצים <b>התקן</b> '
              '<small class="en">(Install)</small>, מקלידים את קוד הנעילה של הטלפון, ושוב <b>התקן</b>.</p>'
              '<p class="note">לא מופיע? <span class="path">הגדרות ← כללי ← VPN וניהול מכשירים</span>. '
              'האזהרה "לא חתום" צפויה בשלב הבדיקה.</p>'),
        _step(4, "מאשרים את תעודת הסינון",
              '<p><span class="path">הגדרות ← כללי ← מידע</span>, גוללים עד למטה ← '
              '<span class="path">הגדרות אמון באישורים</span> <small class="en">(Certificate Trust Settings)</small> ← '
              'מדליקים את <b>FilterPhone CA</b> ← <b>המשך</b>.</p>'
              '<p class="note">בלי השלב הזה אתרים מאובטחים לא ייפתחו. עושים אותו לפני השלב הבא.</p>'),
        _step(5, "פותחים את WireGuard פעם אחת",
              '<p>פותחים את האפליקציה — החיבור <b>FilterPhone</b> כבר מופיע בה ומתחבר לבד '
              '(למעלה במסך יופיע הסימן VPN). מעכשיו הוא מתחבר לבד תמיד, גם אחרי הפעלה מחדש.</p>'),
        _step(6, "חוזרים לדף הזה",
              '<p>הבדיקה למעלה תתעדכן לבד תוך כמה שניות.</p>'),
    ])
    body = (
        f'<h1>התקנה על האייפון</h1><p class="lead">{html.escape(name)} · הקישור תקף לשעתיים</p>'
        '<div class="card status"><div class="row"><span class="dot wait" id="d-vpn"></span>'
        '<span id="t-vpn">מחכה לחיבור המסונן…</span></div>'
        '<div class="row"><span class="dot" id="d-ca"></span><span id="t-ca">תעודת הסינון — ייבדק אחרי החיבור</span></div>'
        '<div class="done" id="done">✓ הסינון פעיל על האייפון הזה</div></div>'
        f'{steps}'
        '<p class="note">שלב בדיקה: כך בודקים את הסינון על אייפון. בשלב הזה אפשר עדיין לכבות את החיבור או '
        'להסיר את הפרופיל בהגדרות — נעילה מלאה דורשת מכשיר מנוהל.</p>'
    )
    script = f"""<script>
(function(){{
  var base={base!r}, probe={PROBE_URL!r}, ready=false, tries=0;
  function set(id,cls,text){{var d=document.getElementById('d-'+id);d.className='dot '+cls;document.getElementById('t-'+id).textContent=text;}}
  function ca(){{
    fetch(probe+'?fp_probe='+Date.now(),{{mode:'no-cors',cache:'no-store'}}).then(function(){{
      set('ca','ok','תעודת הסינון מאושרת');
      document.getElementById('done').style.display='block';
      if(!ready){{ready=true;fetch(base+'/ready',{{method:'POST'}}).catch(function(){{}});}}
    }}).catch(function(){{set('ca','wait','תעודת הסינון עוד לא אושרה — שלב 4');}});
  }}
  function tick(){{
    if(++tries>450) return;
    fetch(base+'/status',{{cache:'no-store'}}).then(function(r){{return r.json();}}).then(function(s){{
      if(s.via_vpn){{set('vpn','ok','מחובר לסינון');ca();}}
      else if(s.connected){{set('vpn','ok','האייפון מחובר לסינון (הדף הזה נפתח ממכשיר אחר)');}}
      else{{set('vpn','wait','מחכה לחיבור המסונן…');set('ca','','תעודת הסינון — ייבדק אחרי החיבור');
            document.getElementById('done').style.display='none';}}
    }}).catch(function(){{}});
  }}
  tick();setInterval(tick,4000);
  document.addEventListener('visibilitychange',function(){{if(!document.hidden)tick();}});
}})();
</script>"""
    return _page("FilterPhone — התקנה על האייפון", body, script)


def profile_headers() -> dict[str, str]:
    return {"Content-Type": iosprofile.CONTENT_TYPE, "Content-Disposition": f'attachment; filename="{PROFILE_NAME}"',
            "Cache-Control": "no-store"}
