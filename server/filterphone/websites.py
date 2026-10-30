"""רמות גלישה בכרום — אילו אתרים נפתחים לכל לקוח (מקור אחד ל-API, לפרוקסי ולבודק האתרים).

מעל סינון התמונות (שפועל בכל אתר) יש "רמת גלישה" — כמה מהאינטרנט פתוח בכלל:

    essential  חיוני בלבד   — ממשלה, בנקים, קופות חולים, תחבורה, תשלומים. בלי חיפוש ובלי שום אתר אחר.
    clean      נקי לגמרי    — החיוני + תורה, כלים ושירותים (PDF, קבצים, טפסים, אישורי הגעה), קניות
                               נקיות, חיפוש Google מסונן, וכל אתר שהבוט בדק ומצא נקי (בלי תמונות של נשים,
                               בלי חדשות, בלי תוכן בעייתי). אתר שעוד לא נבדק — נבדק אוטומטית בכניסה.
    shopping   נקי + קניות  — כמו נקי, ועוד חנויות כלליות (אליאקספרס, שיין, אופנה…) — התמונות מסוננות.
    ""         פתוח (מסונן) — כל מה שלא נחסם בקטגוריות (רשתות, וידאו…). זו ברירת המחדל — כמו עד היום.

לכל רמה אפשר להוסיף קבוצות (extras: חדשות חרדיות, חדשות כלליות, קניות כלליות…) או להוריד קבוצה
(without: למשל "נקי" בלי האתרים שהבוט אישר — רק מה שאדם בדק). אתר בודד — allow_hosts של המכשיר.

האכיפה בפרוקסי — רק על *ניווט* לדף (מסמך ראשי). משאבים של דף מותר (סקריפטים, CDN, API, תמונות)
נטענים כרגיל, כדי שאתרים לא יישברו; התמונות עצמן עוברות את מסנן התמונות כרגיל. כך הרמה משפיעה על
הדפדפן (כרום, הסייר המוגן) ולא על אפליקציות.

"פסק דין" לאתר (verdict) — מה שהבוט (בודק האתרים) או המנהל קבעו לאתר שלא ברשימות: שם של קבוצה
(auto_clean / clean / torah / news_haredi / shopping_general…) או קטגוריה חסומה (images, adult…).
אתר נפתח אם הקבוצה שלו פתוחה ללקוח. הכול כאן טהור — נבדק ביחידה.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------- רמות

LEVEL_ORDER = ("essential", "clean", "shopping", "")
WEB_LEVELS: dict[str, dict[str, str]] = {
    "essential": {
        "title": "חיוני בלבד",
        "short": "רק ממשלה, בנקים, בריאות ותשלומים",
        "desc": "רק אתרים חיוניים: שירותי ממשלה ורשויות, בנקים וכרטיסי אשראי, קופות חולים ובתי חולים, "
                "חברת החשמל, תחבורה ציבורית, חברות סלולר וביטוח, ודפי תשלום מאובטחים. בלי חיפוש ובלי שום "
                "אתר אחר. אפשר להוסיף קבוצות (למשל תורה או כלים).",
    },
    "clean": {
        "title": "נקי לגמרי",
        "short": "אתרים נקיים בלבד — בלי חדשות ובלי תמונות של נשים",
        "desc": "החיוני, ועוד: אתרי תורה ויהדות, כלים ושירותים (PDF, קבצים, טפסים, מיילים, אישורי הגעה), "
                "קניות נקיות (חשמל, מחשבים, סופרמרקט, ציוד משרדי), וחיפוש Google שמציג רק תוצאות מותרות. "
                "כל אתר אחר נבדק אוטומטית בכניסה הראשונה: אתר נקי (בלי תמונות של נשים, בלי חדשות ובלי "
                "תוכן בעייתי) נפתח לבד; אחר — נחסם, עם כפתור 'שליחה לבדיקה'.",
    },
    "shopping": {
        "title": "נקי + קניות",
        "short": "נקי, ועוד חנויות כלליות (התמונות מסוננות)",
        "desc": "כמו 'נקי לגמרי', ועוד חנויות אונליין כלליות: אליאקספרס, שיין, טמו, אמזון, רשתות אופנה "
                "ופארם. התמונות באתרים האלה עוברות את סינון התמונות לפי רמת הסינון.",
    },
    "": {
        "title": "פתוח (עם סינון)",
        "short": "כל האתרים, חוץ ממה שנחסם בקטגוריות",
        "desc": "כל האתרים פתוחים חוץ מהקטגוריות שנחסמו (רשתות חברתיות, וידאו, חדשות…). התמונות עוברות "
                "סינון לפי רמת הסינון, ותוכן למבוגרים חסום תמיד.",
    },
}
WEB_LEVEL_IDS = tuple(LEVEL_ORDER)


@dataclass(frozen=True, slots=True)
class Group:
    id: str
    title: str
    desc: str
    domains: tuple[str, ...] = ()
    levels: tuple[str, ...] = ()        # רמות שבהן הקבוצה פתוחה מעצמה
    extra: bool = True                  # אפשר להוסיף / להוריד לפי לקוח
    infra: bool = False                 # תשתית (תשלום, התחברות) — פתוחה בכל רמה, אי אפשר להוריד
    except_hosts: tuple[str, ...] = ()  # תת-אתרים שלא שייכים לקבוצה (למשל news.google.com)


# ---------------------------------------------------------------- רשימות

_GOV = (
    "gov.il", "muni.il", "idf.il", "knesset.gov.il", "israelpost.co.il", "iec.co.il",
    # חברות מים עירוניות ושירותים
    "mekorot.co.il", "kolzchut.org.il",
)
_TRANSPORT = ("rail.co.il", "egged.co.il", "dan.co.il", "kavim-t.co.il", "bus.co.il", "ravkavonline.co.il",
              "moovitapp.com", "waze.com")
_BANKS = (
    "leumi.co.il", "bankleumi.co.il", "bankhapoalim.co.il", "bitpay.co.il", "discountbank.co.il",
    "telebank.co.il", "mizrahi-tefahot.co.il", "tefahot.co.il", "fibi.co.il", "fibi-online.co.il",
    "mercantile.co.il", "bankjerusalem.co.il", "bank-yahav.co.il", "bankmassad.co.il", "pepper.co.il",
    "onezerobank.com", "payboxapp.com", "boi.org.il",
    # כרטיסי אשראי
    "isracard.co.il", "cal-online.co.il", "max.co.il", "americanexpress.co.il", "diners.co.il",
)
_HEALTH = (
    "clalit.co.il", "maccabi4u.co.il", "meuhedet.co.il", "leumit.co.il", "mdais.org",
    "sheba.co.il", "hadassah.org.il", "laniado.org.il", "mymc.co.il", "szmc.org.il", "tasmc.org.il",
    "rambam.org.il", "assuta.co.il",
)
_PHONE_INSURANCE = (
    "partner.co.il", "cellcom.co.il", "pelephone.co.il", "hot.net.il", "bezeq.co.il", "golantelecom.co.il",
    "019mobile.co.il", "harel-group.co.il", "migdal.co.il", "clalbit.co.il", "menoramivt.co.il", "fnx.co.il",
    "aig.co.il", "ayalon-ins.co.il", "9000000.co.il",
)
_EDU = ("mashov.info", "webtop.co.il", "smartschool.co.il")

# דפי תשלום מאובטחים (סולקים) — אתרים רבים מעבירים לשם לתשלום; גם תרומות (נדרים פלוס וכו')
_PAYMENTS = (
    "cardcom.solutions", "cardcom.co.il", "tranzila.com", "pelecard.biz", "pelecard.com", "meshulam.co.il",
    "grow.business", "rivhit.co.il", "hyp.co.il", "payplus.co.il", "z-credit.com", "upay.co.il",
    "creditguard.co.il", "e-c.co.il", "paypal.com", "paypalobjects.com", "icount.co.il", "greeninvoice.co.il",
    "sumit.co.il", "matara.pro", "jgive.com", "charidy.com", "causematch.com",
    # אימות 3D Secure של חברות האשראי
    "3dsecure.isracard.co.il",
)
# התחברות לחשבונות (מייל, חנות, בנקים דיגיטליים) — בלי זה אי אפשר להיכנס לשום שירות
_ACCOUNTS = (
    "accounts.google.com", "myaccount.google.com", "login.live.com", "login.microsoftonline.com",
    "account.microsoft.com", "appleid.apple.com", "idmsa.apple.com", "login.gov.il",
)
_SEARCH = ("google.com", "google.co.il")
_SEARCH_EXCEPT = (
    "news.google.com", "sites.google.com", "photos.google.com", "play.google.com", "books.google.com",
    "images.google.com", "earth.google.com", "arts.google.com", "gemini.google.com", "bard.google.com",
    "blogger.google.com",
)
_TORAH = (
    "sefaria.org", "sefaria.org.il", "hebrewbooks.org", "daf-yomi.com", "dafyomi.co.il", "kolhalashon.com",
    "yeshiva.org.il", "hamichlol.org.il", "mechon-mamre.org", "otzar.org", "toratemetfreeware.com",
    "dinonline.org", "din.org.il", "torahanytime.com", "yutorah.org", "halachipedia.com", "hebcal.com",
    "myzmanim.com", "beureihatefila.com", "orayta.org", "taryag.co.il", "ohr.edu", "torah.org",
    "dirshu.co.il", "kodesh.snunit.k12.il", "chabad.org", "chabad.org.il", "tehilla.co.il",
    # מילונים ושפה
    "milog.co.il", "morfix.co.il", "hebrew-academy.org.il",
)
_TOOLS = (
    # קבצים ו-PDF
    "ilovepdf.com", "smallpdf.com", "pdf24.org", "sejda.com", "wetransfer.com", "we.tl", "dropbox.com",
    "dropboxusercontent.com", "docs.google.com", "drive.google.com", "drive.usercontent.google.com",
    "docs.googleusercontent.com", "forms.gle", "translate.google.com", "maps.google.com",
    "onedrive.live.com", "1drv.ms", "sharepoint.com", "office.com", "forms.office.com", "forms.cloud.microsoft",
    # מייל
    "mail.google.com", "outlook.live.com", "outlook.office.com", "outlook.office365.com",
    # טפסים, חתימה דיגיטלית ותיאום
    "jotform.com", "typeform.com", "comsign.co.il", "docusign.net", "docusign.com", "calendly.com",
    # אישורי הגעה והזמנות דיגיטליות
    "confirm.co.il", "invite.co.il", "ishurim.co.il", "attending.co.il", "hazmana.co.il",
    # קיצורי קישורים (רק מפנים — היעד עצמו נבדק בנפרד)
    "bit.ly", "tinyurl.com",
    # מדריכי טלפון ועסקים
    "b144.co.il", "d.co.il",
)
_SHOPPING_CLEAN = (
    # מחשבים וחשמל
    "ksp.co.il", "ivory.co.il", "bug.co.il", "zabilo.com", "payngo.co.il", "lastprice.co.il", "traklin.co.il",
    "electricshop.co.il", "plonter.co.il", "tms.co.il",
    # סופרמרקט
    "shufersal.co.il", "rami-levy.co.il", "yochananof.co.il", "osherad.co.il", "carrefour.co.il",
    "hazi-hinam.co.il", "victoryonline.co.il",
    # משרד, בית ועשה זאת בעצמך
    "officedepot.co.il", "kravitz.co.il", "maxstock.co.il", "ace.co.il",
    # השוואת מחירים
    "zap.co.il",
)
_SHOPPING_GENERAL = (
    "aliexpress.com", "aliexpress.us", "temu.com", "shein.com", "shein.co.il", "amazon.com", "amazon.co.uk",
    "amazon.de", "ebay.com", "next.co.il", "nextdirect.com", "terminalx.com", "factory54.co.il", "castro.com",
    "fox.co.il", "renuar.co.il", "golfco.co.il", "super-pharm.co.il", "asos.com", "iherb.com", "ikea.co.il",
    "shilav.co.il", "toysrus.co.il", "365mashbir.co.il", "buyme.co.il", "homecenter.co.il", "yad2.co.il",
    "wallashops.co.il",
)
_NEWS_HAREDI = (
    "bhol.co.il", "kikar.co.il", "jdn.co.il", "hamodia.co.il", "yated.co.il", "ladaat.info", "ladaat.co",
    "kol-barama.co.il", "93fm.co.il", "kore.co.il", "hm-news.co.il", "bizzness.net", "emess.co.il",
    "actualic.co.il", "kooker.co.il", "col.org.il", "chabadinfo.com", "hidabroot.org",
)
_NEWS_GENERAL = (
    "ynet.co.il", "walla.co.il", "mako.co.il", "n12.co.il", "maariv.co.il", "haaretz.co.il", "israelhayom.co.il",
    "themarker.com", "calcalist.co.il", "globes.co.il", "kan.org.il", "13tv.co.il", "reshet.tv", "i24news.tv",
    "0404.co.il", "ice.co.il", "srugim.co.il", "inn.co.il", "kipa.co.il", "davar1.co.il", "news1.co.il",
    "timesofisrael.com", "jpost.com", "cnn.com", "bbc.com", "bbc.co.uk",
    "foxnews.com", "nytimes.com", "news.google.com",
)
_SPORT = ("sport5.co.il", "one.co.il", "sport1.maariv.co.il", "vsport.co.il", "espn.com")

GROUPS: tuple[Group, ...] = (
    Group("essential", "שירותים חיוניים",
          "ממשלה ורשויות, בנקים וכרטיסי אשראי, קופות חולים ובתי חולים, חשמל, תחבורה ציבורית, סלולר, "
          "ביטוח ומערכות בית ספר.",
          _GOV + _TRANSPORT + _BANKS + _HEALTH + _PHONE_INSURANCE + _EDU,
          levels=("essential", "clean", "shopping"), extra=False, infra=True),
    Group("payments", "דפי תשלום מאובטחים", "סולקים ודפי תרומה — כדי שאפשר יהיה לשלם באתרים מותרים.",
          _PAYMENTS, levels=("essential", "clean", "shopping"), extra=False, infra=True),
    Group("accounts", "התחברות לחשבונות", "Google, Microsoft, Apple ו'הזדהות ממשלתית' — מסכי הכניסה בלבד.",
          _ACCOUNTS, levels=("essential", "clean", "shopping"), extra=False, infra=True),
    Group("search", "חיפוש Google (מסונן)",
          "חיפוש רגיל ב-Google, שמציג רק תוצאות שמותרות לרמה שלך. בלי לשונית התמונות, החדשות והסרטונים.",
          _SEARCH, levels=("clean", "shopping"), except_hosts=_SEARCH_EXCEPT),
    Group("torah", "תורה ויהדות", "ספריא, היברובוקס, אוצר החכמה, דף יומי, קול הלשון, ישיבה, המכלול, "
          "זמני היום ולוח שנה, שאלות ותשובות, מילונים.", _TORAH, levels=("clean", "shopping")),
    Group("tools", "כלים ושירותים",
          "PDF וקבצים (iLovePDF, WeTransfer, Google Drive), מייל, טפסים וחתימה דיגיטלית, תרגום ומפות, "
          "אישורי הגעה והזמנות לשמחות, מדריכי טלפון.", _TOOLS, levels=("clean", "shopping")),
    Group("shopping_clean", "קניות נקיות",
          "חנויות בלי תמונות של אנשים כמעט: מחשבים וחשמל (KSP, באג, אייבורי), סופרמרקט (שופרסל, רמי לוי), "
          "ציוד משרדי ובית (אופיס דיפו, קרביץ, מקס סטוק, ACE), השוואת מחירים.",
          _SHOPPING_CLEAN, levels=("clean", "shopping")),
    Group("auto_clean", "אתרים נקיים שהבוט בדק",
          "כל אתר שלא ברשימות נבדק אוטומטית בכניסה הראשונה (טקסט ותמונות). אתר נקי — נפתח לבד. "
          "בלי זה נפתחים רק אתרים שאדם בדק ואישר.", levels=("clean", "shopping")),
    Group("clean", "אתרים שאושרו בבדיקה אנושית", "אתרים שהמנהל בדק ואישר כנקיים — פתוחים מרמת 'נקי'.",
          levels=("clean", "shopping"), extra=False),
    Group("shopping_general", "קניות כלליות",
          "אליאקספרס, שיין, טמו, אמזון, איביי, רשתות אופנה, פארם ויד 2 — באתרים האלה יש הרבה תמונות של "
          "אנשים; הן עוברות את סינון התמונות.", _SHOPPING_GENERAL, levels=("shopping",)),
    Group("news_haredi", "חדשות לציבור החרדי",
          "בחדרי חרדים, כיכר השבת, JDN, המודיע, יתד, לדעת, קול ברמה, קול חי, כל רגע ועוד.",
          _NEWS_HAREDI),
    Group("news_general", "חדשות לציבור הרחב", "ynet, וואלה, מאקו, N12, מעריב, ישראל היום, ערוץ 7, כאן ועוד.",
          _NEWS_GENERAL),
    Group("sport", "ספורט", "ספורט 5, ONE, ספורט 1 ועוד.", _SPORT),
)
GROUP_BY_ID: dict[str, Group] = {g.id: g for g in GROUPS}
assert len(GROUP_BY_ID) == len(GROUPS), "duplicate group id"

# פסקי דין שאינם קבוצה — לעולם לא נפתחים ברמות של רשימה לבנה
BLOCKED_VERDICTS = {
    "images": "יש באתר תמונות של נשים",
    "news": "אתר חדשות",
    "shopping": "חנות עם תמונות של אנשים",
    "social": "רשת חברתית",
    "video": "וידאו ובידור",
    "adult": "תוכן למבוגרים",
    "gambling": "הימורים",
    "dating": "היכרויות",
    "games": "משחקים",
    "ai": "בינה מלאכותית",
    "proxy": "עקיפת סינון",
    "blocked": "נחסם על ידי המנהל",
    "error": "לא הצלחנו לבדוק את האתר",
}
VERDICTS = tuple(GROUP_BY_ID) + tuple(BLOCKED_VERDICTS)
VERDICT_TITLES = {**{g.id: g.title for g in GROUPS}, **BLOCKED_VERDICTS}

# אתרים שמארחים תוכן של אנשים רבים בכתובת אחת (לפי נתיב) — הבוט לא מאשר אותם אוטומטית
NO_AUTO = ("sites.google.com", "docs.google.com", "drive.google.com", "github.com", "medium.com",
           "substack.com", "notion.site", "linktr.ee", "archive.org", "web.archive.org", "translate.goog",
           "webcache.googleusercontent.com", "googleusercontent.com", "blogspot.com", "wordpress.com",
           "wixsite.com", "weebly.com", "tumblr.com", "pages.dev", "vercel.app", "netlify.app", "github.io")

_DOMAIN_GROUP: dict[str, str] = {}
for _g in GROUPS:
    for _d in _g.domains:
        _DOMAIN_GROUP.setdefault(_d, _g.id)
_EXCEPT: dict[str, str] = {h: g.id for g in GROUPS for h in g.except_hosts}


def _suffixes(host: str):
    """a.b.c.co.il → a.b.c.co.il, b.c.co.il, c.co.il, co.il (בלי הסיומת האחרונה לבד)."""
    parts = host.lower().rstrip(".").split(".")
    for i in range(len(parts) - 1):
        yield ".".join(parts[i:])


def norm_host(host: str) -> str:
    h = (host or "").strip().lower().rstrip(".")
    return h[4:] if h.startswith("www.") else h


def group_of(host: str) -> str | None:
    """הקבוצה של האתר לפי הרשימות (None = לא ברשימות). הכתובת הספציפית ביותר קובעת: תת-אתר שמוחרג
    מקבוצה (sites.google.com מ"חיפוש") לא שייך לה — אלא אם הוא רשום בעצמו בקבוצה אחרת."""
    excepted = None
    for s in _suffixes(host):
        if excepted is None and s in _EXCEPT:
            excepted = _EXCEPT[s]
        g = _DOMAIN_GROUP.get(s)
        if g:
            return None if g == excepted else g
    return None


def lookup(table: dict[str, str], host: str) -> str | None:
    """פסק הדין של האתר או של אחד מאתרי האם שלו (shop.example.co.il → example.co.il)."""
    for s in _suffixes(host):
        v = table.get(s)
        if v:
            return v
    return None


def no_auto(host: str) -> bool:
    return any(s in NO_AUTO for s in _suffixes(host))


# ---------------------------------------------------------------- רמה + תוספות → קבוצות

def resolve_groups(level: str, extras: list[str] | tuple[str, ...] = (),
                   without: list[str] | tuple[str, ...] = ()) -> tuple[str, ...]:
    """הקבוצות הפתוחות ללקוח. רמה פתוחה ("") — ריק (שם לא אוכפים רשימה לבנה)."""
    if level not in WEB_LEVELS or level == "":
        return ()
    out = {g.id for g in GROUPS if level in g.levels or g.infra}
    out |= {e for e in extras if e in GROUP_BY_ID and GROUP_BY_ID[e].extra}
    out -= {w for w in without if w in GROUP_BY_ID and not GROUP_BY_ID[w].infra}
    return tuple(g.id for g in GROUPS if g.id in out)


def clean_list(values, allowed) -> list[str]:
    return sorted({str(v) for v in (values or []) if str(v) in allowed})


@dataclass(frozen=True, slots=True)
class WebPolicy:
    """מה שהפרוקסי אוכף למכשיר (נבנה מ-"web" במדיניות שב-Redis)."""
    level: str = ""
    groups: frozenset[str] = frozenset()
    allow: tuple[str, ...] = ()         # אתרים שהמנהל פתח למכשיר הזה

    @property
    def active(self) -> bool:
        return self.level in WEB_LEVELS and self.level != ""

    @property
    def auto_check(self) -> bool:
        return "auto_clean" in self.groups

    @classmethod
    def from_json(cls, d) -> WebPolicy:
        if not isinstance(d, dict) or d.get("level") not in WEB_LEVELS:
            return cls()
        return cls(level=d["level"], groups=frozenset(str(g) for g in d.get("groups", []) if g in GROUP_BY_ID),
                   allow=tuple(norm_host(str(h)) for h in d.get("allow", [])[:500] if h))

    def allowed_by_device(self, host: str) -> bool:
        return any(s in self.allow for s in _suffixes(host))

    def decide(self, host: str, verdict: str | None) -> str:
        """allow / check (לבדוק עכשיו — הבוט) / block. [verdict] — פסק הדין של האתר (או None)."""
        if not self.active:
            return "allow"
        host = norm_host(host)
        if not host:
            return "block"
        if self.allowed_by_device(host):
            return "allow"
        g = group_of(host)
        if g is not None:
            return "allow" if g in self.groups else "block"
        if verdict:
            return "allow" if verdict in self.groups else "block"
        if self.auto_check and not no_auto(host):
            return "check"
        return "block"

    def visible(self, host: str, verdict: str | None) -> bool:
        """האם להציג תוצאת חיפוש לאתר: מה שייחסם — מוסתר; מה שעוד לא נבדק — מוצג (ייבדק בלחיצה)."""
        return self.decide(host, verdict) != "block"


def public_catalog() -> dict:
    """לממשק הניהול ולאפליקציה: הרמות והקבוצות (בלי רשימות האתרים המלאות)."""
    return {
        "levels": [{"id": k, **WEB_LEVELS[k]} for k in LEVEL_ORDER],
        "groups": [{"id": g.id, "title": g.title, "desc": g.desc, "levels": list(g.levels), "extra": g.extra,
                    "infra": g.infra, "count": len(g.domains)} for g in GROUPS],
        "verdicts": [{"id": v, "title": VERDICT_TITLES[v]} for v in VERDICTS],
    }
