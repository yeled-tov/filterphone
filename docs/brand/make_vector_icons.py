"""האייקונים של FilterPhone (מגן FP) ושל הסייר המוגן (גלובוס + תג מגן) כ-VectorDrawable של אנדרואיד:
שכבות רקע / חזית / מונוכרום לאייקון מסתגל — חדים בכל גודל, בלי קבצי PNG לכל צפיפות.

המקור: filterphone-icon.svg / safe-browser-icon.svg (אותו שרטוט, 1024×1024 = ה-72dp הגלויים).
שכבה של אייקון מסתגל היא 108dp, ולכן ה-viewport הוא 1536 והשרטוט מוזז ב-256 (במרכז, בתוך האזור הבטוח).
הרצה: python3 docs/brand/make_vector_icons.py app/android/app/src/main/res
"""
import re
import sys
from pathlib import Path

RES = Path(sys.argv[1])
NS = 'xmlns:android="http://schemas.android.com/apk/res/android" xmlns:aapt="http://schemas.android.com/aapt"'
SHIELD = ("M512 170 C590 214 676 236 774 240 Q806 241 806 274 L806 500 C806 672 686 796 512 862 "
          "C338 796 218 672 218 500 L218 274 Q218 241 250 240 C348 236 434 214 512 170 Z")


def vector(body: str) -> str:
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<!-- נוצר על ידי docs/brand/make_vector_icons.py — לא לערוך ביד -->\n'
            f'<vector {NS}\n    android:width="108dp" android:height="108dp"\n'
            f'    android:viewportWidth="1536" android:viewportHeight="1536">\n{body}</vector>\n')


def linear(attr, x1, y1, x2, y2, c1, c2):
    return (f'        <aapt:attr name="android:{attr}">\n'
            f'            <gradient android:type="linear" android:startX="{x1}" android:startY="{y1}" '
            f'android:endX="{x2}" android:endY="{y2}" android:startColor="{c1}" android:endColor="{c2}"/>\n'
            f'        </aapt:attr>\n')


def background(top, bottom):
    rect = "M0,0 H1536 V1536 H0 Z"
    glow = ('        <aapt:attr name="android:fillColor">\n'
            '            <gradient android:type="radial" android:centerX="563" android:centerY="461" android:gradientRadius="921">\n'
            '                <item android:offset="0" android:color="#38FFFFFF"/>\n'
            '                <item android:offset="0.6" android:color="#00FFFFFF"/>\n'
            '            </gradient>\n        </aapt:attr>\n')
    return vector(f'    <path android:pathData="{rect}">\n{linear("fillColor", 0, 0, 1536, 1536, top, bottom)}    </path>\n'
                  f'    <path android:pathData="{rect}">\n{glow}    </path>\n')


def rrect(x, y, w, h, r, t=lambda x, y: (x, y), s=1.0):
    """מלבן מעוגל כנתיב, אחרי הזזה/הגדלה אחידה [t] (קנה מידה [s] — לרדיוס)."""
    def P(px, py):
        a, b = t(px, py)
        return f"{a:.2f},{b:.2f}"
    R = r * s
    return (f"M{P(x + r, y)} L{P(x + w - r, y)} A{R:.2f},{R:.2f} 0 0 1 {P(x + w, y + r)} L{P(x + w, y + h - r)} "
            f"A{R:.2f},{R:.2f} 0 0 1 {P(x + w - r, y + h)} L{P(x + r, y + h)} A{R:.2f},{R:.2f} 0 0 1 {P(x, y + h - r)} "
            f"L{P(x, y + r)} A{R:.2f},{R:.2f} 0 0 1 {P(x + r, y)} Z")


def rr(x, y, w, h, tl, tr, br, bl, t, s):
    """מלבן עם רדיוס לכל פינה בנפרד (0 = פינה ישרה), אחרי הזזה/הגדלה אחידה [t] (קנה מידה [s])."""
    def P(px, py):
        a, b = t(px, py)
        return f"{a:.2f},{b:.2f}"
    def arc(r, px, py):
        return f" A{r * s:.2f},{r * s:.2f} 0 0 1 {P(px, py)}" if r else ""
    return (f"M{P(x + tl, y)} L{P(x + w - tr, y)}{arc(tr, x + w, y + tr)} L{P(x + w, y + h - br)}"
            f"{arc(br, x + w - br, y + h)} L{P(x + bl, y + h)}{arc(bl, x, y + h - bl)} L{P(x, y + tl)}{arc(tl, x + tl, y)} Z")


def letters(t=lambda x, y: (x, y), s=1.0, x=334, y=330, h=300, stem=70):
    """F ו-P (כמו ב-SVG) כנתיב אחד — חלקים שלא חופפים (נוגעים זה בזה), כדי ש-evenOdd יעבוד: בחזית החור של
    ה-P בלבד נשאר שקוף, ובשכבה המונוכרומטית האותיות הן חורים במגן."""
    k = h / 300
    r = 18 * k
    px = x + 180 * k
    br = 89 * k
    parts = [
        rr(x, y, stem, h, r, 0, r, r, t, s),                              # F: הקו האנכי
        rr(x + stem, y, 150 * k - stem, 66 * k, 0, r, r, 0, t, s),        # F: הקו העליון
        rr(x + stem, y + 120 * k, 128 * k - stem, 60 * k, 0, r, r, 0, t, s),  # F: הקו האמצעי
        rr(px, y, stem, h, r, 0, r, r, t, s),                             # P: הקו האנכי
    ]

    def P(a, b):
        u, v = t(a, b)
        return f"{u:.2f},{v:.2f}"
    parts.append(f"M{P(px + stem, y)} L{P(px + 86 * k, y)} A{br * s:.2f},{br * s:.2f} 0 0 1 {P(px + 86 * k, y + 178 * k)} "
                 f"L{P(px + stem, y + 178 * k)} Z")                       # P: העיגול
    hr = 27 * k * s
    parts.append(f"M{P(px + stem, y + 62 * k)} L{P(px + 84 * k, y + 62 * k)} A{hr:.2f},{hr:.2f} 0 0 1 "
                 f"{P(px + 84 * k, y + 116 * k)} L{P(px + stem, y + 116 * k)} Z")   # P: החור
    return " ".join(parts)


def shield_tf(scale, cy=516):
    return lambda x, y: (512 + scale * (x - 512), cy + scale * (y - 516))


def shield_path(scale, cy=516):
    """נתיב המגן אחרי הגדלה סביב המרכז (לשכבה המונוכרומטית — הכול בנתיב אחד)."""
    tf = shield_tf(scale, cy)
    toks = re.findall(r"[A-Za-z]|-?\d*\.?\d+", SHIELD)
    out, nums = [], []
    for tok in toks:
        if tok[0].isalpha():
            if nums:
                out.append(" ".join(f"{a:.2f},{b:.2f}" for a, b in (tf(nums[i], nums[i + 1]) for i in range(0, len(nums), 2))))
                nums = []
            out.append(tok)
        else:
            nums.append(float(tok))
    if nums:
        out.append(" ".join(f"{a:.2f},{b:.2f}" for a, b in (tf(nums[i], nums[i + 1]) for i in range(0, len(nums), 2))))
    return " ".join(out)


def group(scale, cy=516, body=""):
    return (f'        <group android:pivotX="512" android:pivotY="516" android:scaleX="{scale}" android:scaleY="{scale}" '
            f'android:translateY="{cy - 516}">\n{body}        </group>\n')


# ------------------------------------------------------------------ FilterPhone — מגן FP
def fp_foreground():
    ring = (f'            <path android:pathData="{SHIELD}" android:strokeWidth="26" android:strokeLineJoin="round" android:fillColor="#00000000">\n'
            + linear("strokeColor", 512, 170, 512, 862, "#FFD84D", "#F2A900").replace("        <", "            <") +
            '            </path>\n')
    shadow = (f'            <path android:pathData="{SHIELD}" android:fillColor="#1F021A33"/>\n')
    white = (f'            <path android:pathData="{SHIELD}">\n'
             + linear("fillColor", 512, 170, 512, 862, "#FFFFFF", "#E4F1FB").replace("        <", "            <") +
             '            </path>\n')
    lt = lambda x, y: (512 + 0.9 * (x - 512), 494 + 0.9 * (y - 480))
    ink = (f'        <path android:fillType="evenOdd" android:pathData="{letters(lt, 0.9)}">\n'
           + linear("fillColor", 512, 791 - 494, 512, 791, "#1688D6", "#0A4C86") + '        </path>\n')
    body = ('    <group android:translateX="256" android:translateY="256">\n'
            + group(0.86, body=ring)
            + group(0.79, 530, body=shadow)
            + group(0.78, body=white)
            + ink + '    </group>\n')
    return vector(body)


def fp_monochrome():
    lt = lambda x, y: (512 + 0.9 * (x - 512), 494 + 0.9 * (y - 480))
    d = shield_path(0.82) + " " + letters(lt, 0.9)
    return vector(f'    <group android:translateX="256" android:translateY="256">\n'
                  f'        <path android:fillType="evenOdd" android:fillColor="#FFFFFFFF" android:pathData="{d}"/>\n    </group>\n')


# ------------------------------------------------------------------ הסייר המוגן — גלובוס + תג
def circle(cx, cy, r):
    return f"M{cx - r},{cy} A{r},{r} 0 1 1 {cx + r},{cy} A{r},{r} 0 1 1 {cx - r},{cy} Z"


def ellipse(cx, cy, rx, ry):
    return f"M{cx - rx},{cy} A{rx},{ry} 0 1 1 {cx + rx},{cy} A{rx},{ry} 0 1 1 {cx - rx},{cy} Z"


def browser_foreground():
    globe = " ".join([circle(482, 482, 222), ellipse(482, 482, 96, 222), "M260,482 L704,482",
                      "M290,372 Q482,410 674,372", "M290,592 Q482,554 674,592"])
    body = ('    <group android:translateX="256" android:translateY="256">\n'
            f'        <path android:pathData="{globe}" android:strokeColor="#FFFFFFFF" android:strokeWidth="34" '
            'android:strokeLineCap="round" android:fillColor="#00000000"/>\n'
            f'        <path android:pathData="{circle(704, 718, 154)}" android:fillColor="#26021A33"/>\n'
            f'        <path android:pathData="{circle(704, 704, 150)}" android:fillColor="#FF0A4C86"/>\n'
            '        <group android:pivotX="512" android:pivotY="516" android:scaleX="0.31" android:scaleY="0.31" '
            'android:translateX="192" android:translateY="196">\n'
            f'            <path android:pathData="{SHIELD}">\n'
            + linear("fillColor", 512, 170, 512, 862, "#FFD84D", "#F2A900").replace("        <", "            <") +
            '            </path>\n        </group>\n'
            '        <path android:pathData="M664,708 L694,738 L748,680" android:strokeColor="#FF0A4C86" android:strokeWidth="26" '
            'android:strokeLineCap="round" android:strokeLineJoin="round" android:fillColor="#00000000"/>\n'
            '    </group>\n')
    return vector(body)


def adaptive(bg, fg, mono=None, comment=""):
    m = f'    <monochrome android:drawable="@drawable/{mono}" />\n' if mono else ""
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<!-- {comment} -->\n'
            f'<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
            f'    <background android:drawable="@drawable/{bg}" />\n    <foreground android:drawable="@drawable/{fg}" />\n{m}'
            f'</adaptive-icon>\n')


d = RES / "drawable"
d.mkdir(exist_ok=True)
(d / "ic_launcher_bg.xml").write_text(background("#1A9BE8", "#08497F"))
(d / "ic_launcher_fg.xml").write_text(fp_foreground())
(d / "ic_launcher_mono.xml").write_text(fp_monochrome())
(d / "ic_browser_bg.xml").write_text(background("#36B0F2", "#0A5A97"))
(d / "ic_browser_fg.xml").write_text(browser_foreground())
a = RES / "mipmap-anydpi-v26"
a.mkdir(exist_ok=True)
(a / "ic_launcher.xml").write_text(adaptive("ic_launcher_bg", "ic_launcher_fg", "ic_launcher_mono",
    "האייקון של FilterPhone (מגן FP, כחול-זהב) — אייקון מסתגל וקטורי + שכבה מונוכרומטית (אנדרואיד 13+)"))
(a / "ic_browser.xml").write_text(adaptive("ic_browser_bg", "ic_browser_fg", None,
    "האייקון של הסייר המוגן (גלובוס + תג המגן של FilterPhone) — סמל משלו במסך הבית ובאחרונים"))
print("ok")
