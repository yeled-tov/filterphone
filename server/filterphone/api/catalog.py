"""קטלוג אפשרויות החסימה — מקור אחד לממשק הניהול, לטופס ההרשמה באפליקציה ולאכיפה.

לכל אפשרות שלוש דרכי אכיפה, והיא משתמשת בכל מה שרלוונטי לה:
  hosts   — חסימה ברשת: DNS (AdGuard, לפי כתובת המכשיר) + הפרוקסי (SNI / Host).
            עובד על כל אפליקציה ודפדפן, בלי שום התקנה נוספת בטלפון.
  apps    — חסימה בתוך הטלפון: "מגן FilterPhone" (נגישות / מנהל מכשיר) לא נותן לפתוח
            את האפליקציה. נחוץ כשאי אפשר להבדיל ברשת (דפדפנים, טלגרם, משחקים).
  screens — מסך מסוים בתוך אפליקציה (למשל צפייה בסטטוסים בוואטסאפ).
ועוד דגלים לפרוקסי: images=block (בלי תמונות), video (בלי וידאו), paths (כתובות מסוימות).

כתובת 'example.com' חוסמת גם כל תת-דומיין. '*' = כל רצף (למשל media*.whatsapp.net).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

LEVELS = {
    0: {"name": "כבוי", "short": "בלי סינון תמונות",
        "desc": "רק סינון האתרים (DNS) פעיל. תמונות לא נבדקות."},
    1: {"name": "מחמיר", "short": "כל תמונה של אישה נחסמת",
        "desc": "כל תמונה של אישה או ילדה נחסמת, גם בלבוש צנוע. תמונות של גברים, נופים ומוצרים עוברות. "
                "באפליקציות שאי אפשר לסנן — תמונות לא נטענות."},
    2: {"name": "בינוני", "short": "לבוש צנוע עובר, חשיפה נצבעת",
        "desc": "תמונות בלבוש צנוע עוברות. עור חשוף נצבע בשחור לפי צורת הגוף (ידיים, כתפיים, רגליים "
                "וכו'), ובלבוש חשוף מאוד — כל הגוף חוץ מהפנים. עירום נחסם לגמרי."},
    3: {"name": "בסיסי", "short": "רק עירום נחסם",
        "desc": "רק תמונות עירום מפורש נחסמות. כל השאר עובר."},
}

CATEGORIES = [
    {"id": "images", "title": "תמונות ווידאו", "desc": "מעבר לרמת הסינון: בלי תמונות בכלל, בלי סרטונים."},
    {"id": "messaging", "title": "וואטסאפ והודעות", "desc": "תמונות פרופיל, סטטוסים, מדיה — או חסימה מלאה."},
    {"id": "social", "title": "רשתות חברתיות", "desc": "חסימה מלאה — באפליקציה ובדפדפן."},
    {"id": "google", "title": "גוגל ואנדרואיד", "desc": "חנות האפליקציות, הפיד של גוגל, חיפוש תמונות."},
    {"id": "browsers", "title": "דפדפנים", "desc": "דפדפנים שלא עוברים סינון תמונות, או כל הגלישה."},
    {"id": "video", "title": "וידאו, מוזיקה ובידור", "desc": "יוטיוב, נטפליקס, ספוטיפיי ועוד."},
    {"id": "shopping", "title": "קניות", "desc": "חנויות אונליין עם הרבה תמונות שקשה לסנן באפליקציה."},
    {"id": "news", "title": "חדשות וספורט", "desc": "אתרי חדשות כלליים."},
    {"id": "ai", "title": "בינה מלאכותית", "desc": "צ'אטבוטים ומחוללי תמונות."},
    {"id": "games", "title": "משחקים", "desc": "משחקים אונליין וחנויות משחקים."},
    {"id": "dating", "title": "היכרויות", "desc": "אפליקציות ואתרי היכרויות."},
    {"id": "protection", "title": "הגנה מפני עקיפה", "desc": "סגירת הדרכים לעקוף את הסינון."},
]


@dataclass(frozen=True, slots=True)
class Option:
    id: str
    category: str
    title: str
    desc: str = ""
    hosts: tuple[str, ...] = ()
    apps: tuple[str, ...] = ()
    screens: tuple[tuple[str, tuple[str, ...]], ...] = ()
    paths: tuple[tuple[str, str], ...] = ()     # (אתר, ביטוי רגולרי על נתיב+query)
    images: str = ""                            # "block" = כל התמונות
    video: bool = False
    popular: bool = False                       # מודגש בטופס ההרשמה ("הכי מבוקשים")
    warn: str = ""                              # מה עוד ייפגע — מוצג בממשק
    needs_guard: bool = False                   # עובד רק כשמגן FilterPhone פעיל בטלפון

    @property
    def mechanisms(self) -> list[str]:
        m = []
        if self.hosts or self.paths:
            m.append("network")
        if self.apps or self.screens:
            m.append("device")
        if self.images or self.video:
            m.append("proxy")
        return m

    def public(self) -> dict:
        """מה שהאפליקציה מקבלת לטופס ההרשמה (בלי פרטי אכיפה)."""
        return {"id": self.id, "category": self.category, "title": self.title, "desc": self.desc,
                "popular": self.popular, "warn": self.warn}

    def admin(self) -> dict:
        return {**self.public(), "hosts": list(self.hosts), "apps": list(self.apps),
                "screens": [{"package": p, "match": list(m)} for p, m in self.screens],
                "mechanisms": self.mechanisms, "needs_guard": self.needs_guard,
                "images": self.images, "video": self.video}


WA = ("com.whatsapp", "com.whatsapp.w4b")
BROWSERS_OTHER = (
    "com.sec.android.app.sbrowser", "org.mozilla.firefox", "org.mozilla.firefox_beta", "org.mozilla.focus",
    "org.mozilla.fenix", "com.opera.browser", "com.opera.mini.native", "com.opera.gx", "com.microsoft.emmx",
    "com.brave.browser", "com.duckduckgo.mobile.android", "com.UCMobile.intl", "com.uc.browser.en",
    "com.mi.globalbrowser", "com.android.browser", "com.vivaldi.browser", "com.kiwibrowser.browser",
    "com.yandex.browser", "com.ecosia.android", "org.torproject.torbrowser", "com.cloudmosa.puffinFree",
    "com.chrome.beta", "com.chrome.dev", "com.chrome.canary", "com.huawei.browser", "com.heytap.browser",
)

OPTIONS: tuple[Option, ...] = (
    # ------------------------------------------------------------ תמונות ווידאו
    Option("images_off", "images", "חסימת כל התמונות",
           "גלישה בלי תמונות בכלל: כל תמונה בדפדפן מוחלפת במלבן אפור, בלי קשר לרמת הסינון.",
           images="block"),
    Option("video_off", "images", "חסימת וידאו בדפדפן",
           "סרטונים באתרים לא נטענים (גם נגנים מוטמעים). אפליקציות וידאו נחסמות בנפרד בקטגוריית הבידור.",
           video=True, popular=True),

    # ------------------------------------------------------------ וואטסאפ והודעות
    Option("whatsapp_profile", "messaging", "תמונות פרופיל בוואטסאפ",
           "תמונות הפרופיל של אנשי הקשר והקבוצות לא נטענות. הודעות, שיחות, קבצים ותמונות בצ'אט — כרגיל.",
           hosts=("pps.whatsapp.net",), popular=True),
    Option("whatsapp_status", "messaging", "סטטוסים בוואטסאפ",
           "אי אפשר לפתוח סטטוסים. נאכף בטלפון עצמו (מגן FilterPhone), כי וואטסאפ מוריד סטטוסים מאותם "
           "שרתים כמו תמונות בצ'אט. לחסימה מלאה גם של התמונות — 'מדיה בוואטסאפ'.",
           screens=tuple((p, ("status.playback", "StatusPlayback", "statusplayback")) for p in WA),
           popular=True, needs_guard=True),
    Option("whatsapp_media", "messaging", "מדיה בוואטסאפ (תמונות, סרטונים, סטטוסים)",
           "תמונות, סרטונים וסטטוסים לא יורדים. ההודעות הכתובות והשיחות עובדות.",
           hosts=("mmg.whatsapp.net", "mmg-fna.whatsapp.net", "media*.whatsapp.net"),
           warn="וואטסאפ מוריד גם הודעות קוליות ומסמכים מאותם שרתים — גם הם לא ייפתחו."),
    Option("whatsapp", "messaging", "וואטסאפ — חסימה מלאה",
           "וואטסאפ ו-WhatsApp Business לא עובדים בכלל.",
           hosts=("whatsapp.com", "whatsapp.net", "wa.me", "whatsapp.biz"), apps=WA),
    Option("telegram", "messaging", "טלגרם",
           "האפליקציה והאתר. טלגרם מתחבר גם לכתובות קבועות — לכן נחסם גם בטלפון עצמו.",
           hosts=("telegram.org", "t.me", "telegram.me", "telesco.pe", "tdesktop.com", "telegra.ph",
                  "telegram-cdn.org", "cdn-telegram.org"),
           apps=("org.telegram.messenger", "org.telegram.messenger.web", "org.thunderdog.challegram",
                 "org.telegram.plus", "nekox.messenger"), needs_guard=True),
    Option("viber", "messaging", "ויבר", "האפליקציה והקהילות שבה.",
           hosts=("viber.com",), apps=("com.viber.voip",)),

    # ------------------------------------------------------------ רשתות חברתיות
    Option("facebook", "social", "פייסבוק ומסנג'ר", "",
           hosts=("facebook.com", "fb.com", "fb.me", "fbcdn.net", "fbsbx.com", "facebook.net",
                  "messenger.com", "m.me", "fb.watch"),
           apps=("com.facebook.katana", "com.facebook.orca", "com.facebook.lite", "com.facebook.mlite"),
           popular=True),
    Option("instagram", "social", "אינסטגרם", "",
           hosts=("instagram.com", "cdninstagram.com", "ig.me", "igcdn.com", "instagr.am"),
           apps=("com.instagram.android", "com.instagram.lite"), popular=True),
    Option("tiktok", "social", "טיקטוק", "",
           hosts=("tiktok.com", "tiktokcdn.com", "tiktokcdn-us.com", "tiktokcdn-eu.com", "tiktokv.com",
                  "tiktokv.eu", "tiktokv.us", "tiktokw.us", "ttlivecdn.com", "muscdn.com", "musical.ly",
                  "byteoversea.com", "ibytedtos.com", "ibyteimg.com", "snssdk.com", "tiktokrow-cdn.com"),
           apps=("com.zhiliaoapp.musically", "com.ss.android.ugc.trill", "com.zhiliaoapp.musically.go"),
           popular=True),
    Option("twitter", "social", "X (טוויטר)", "",
           hosts=("x.com", "twitter.com", "twimg.com", "t.co", "twttr.com", "pscp.tv", "periscope.tv"),
           apps=("com.twitter.android",)),
    Option("threads", "social", "Threads", "",
           hosts=("threads.net", "threads.com"), apps=("com.instagram.barcelona",)),
    Option("snapchat", "social", "סנאפצ'ט", "",
           hosts=("snapchat.com", "sc-cdn.net", "snapkit.co", "sc-static.net", "snap-dev.net"),
           apps=("com.snapchat.android",)),
    Option("pinterest", "social", "פינטרסט", "",
           hosts=("pinterest.com", "pinterest.co.il", "pinimg.com", "pin.it"), apps=("com.pinterest",)),
    Option("reddit", "social", "רדיט", "",
           hosts=("reddit.com", "redd.it", "redditmedia.com", "redditstatic.com"),
           apps=("com.reddit.frontpage",)),
    Option("linkedin", "social", "לינקדאין", "",
           hosts=("linkedin.com", "licdn.com", "lnkd.in"), apps=("com.linkedin.android",)),
    Option("tumblr", "social", "טאמבלר", "", hosts=("tumblr.com",), apps=("com.tumblr",)),
    Option("discord", "social", "דיסקורד", "",
           hosts=("discord.com", "discord.gg", "discordapp.com", "discordapp.net", "discord.media",
                  "discordcdn.com"), apps=("com.discord",)),

    # ------------------------------------------------------------ גוגל ואנדרואיד
    Option("play_store", "google", "חנות Google Play",
           "אי אפשר להתקין אפליקציות חדשות מהחנות.",
           hosts=("play.google.com", "play.googleapis.com", "play-fe.googleapis.com",
                  "play-lh.googleusercontent.com", "market.android.com"),
           apps=("com.android.vending",), popular=True,
           warn="גם עדכוני אפליקציות דרך החנות ייעצרו. מומלץ לחסום יחד גם 'אתרי הורדת APK'."),
    Option("google_discover", "google", "הפיד של גוגל (Discover)",
           "הכתבות עם התמונות במסך הבית של אפליקציית גוגל ובדף הפתיחה של Chrome.",
           hosts=("discover-pa.googleapis.com",), popular=True),
    Option("google_thumbs", "google", "תמונות ממוזערות בגוגל",
           "תוצאות חיפוש, חדשות וקניות בגוגל — בלי התמונות הקטנות (גם באפליקציית גוגל).",
           hosts=tuple(f"encrypted-tbn{i}.gstatic.com" for i in range(4))
           + tuple(f"t{i}.gstatic.com" for i in range(4))),
    Option("google_images", "google", "חיפוש תמונות בגוגל",
           "הלשונית 'תמונות' בחיפוש גוגל ובבינג חסומה. החיפוש הרגיל עובד.",
           hosts=("images.google.com",),
           paths=(("google.*", r"[?&](?:tbm=isch|udm=2)(?:&|$)"), ("bing.com", r"^/images/"))),
    Option("google_app", "google", "אפליקציית גוגל",
           "האפליקציה עם הפיד והחיפוש הקולי. החיפוש בכרום ממשיך לעבוד.",
           apps=("com.google.android.googlequicksearchbox",), needs_guard=True),
    Option("google_photos", "google", "Google Photos", "",
           hosts=("photos.google.com", "photos.app.goo.gl"), apps=("com.google.android.apps.photos",)),

    # ------------------------------------------------------------ דפדפנים
    Option("browsers_other", "browsers", "דפדפנים חוץ מ-Chrome",
           "Firefox, Samsung Internet, Opera, DuckDuckGo ועוד לא נותנים לסנן תמונות. "
           "חוסמים אותם ומשאירים את Chrome, שעובר סינון מלא.",
           apps=BROWSERS_OTHER, popular=True, needs_guard=True),
    Option("browsers_all", "browsers", "כל הדפדפנים (גם Chrome)",
           "אין גלישה בכלל. אפליקציות אחרות ממשיכות לעבוד.",
           apps=("com.android.chrome", *BROWSERS_OTHER), needs_guard=True),

    # ------------------------------------------------------------ וידאו, מוזיקה ובידור
    Option("youtube", "video", "יוטיוב", "האפליקציה, האתר והסרטונים המוטמעים באתרים אחרים.",
           hosts=("youtube.com", "youtu.be", "ytimg.com", "googlevideo.com", "youtube-nocookie.com",
                  "youtubei.googleapis.com", "youtube.googleapis.com", "yt3.ggpht.com", "youtube.co.il"),
           apps=("com.google.android.youtube", "com.google.android.apps.youtube.kids",
                 "com.google.android.apps.youtube.music", "app.revanced.android.youtube",
                 "com.vanced.android.youtube"), popular=True),
    Option("netflix", "video", "נטפליקס", "",
           hosts=("netflix.com", "netflix.net", "nflxvideo.net", "nflximg.net", "nflximg.com", "nflxext.com",
                  "nflxso.net"), apps=("com.netflix.mediaclient",)),
    Option("disney", "video", "דיסני+", "",
           hosts=("disneyplus.com", "disney-plus.net", "bamgrid.com", "dssott.com"),
           apps=("com.disney.disneyplus",)),
    Option("twitch", "video", "טוויץ'", "",
           hosts=("twitch.tv", "ttvnw.net", "jtvnw.net", "twitchcdn.net", "twitchsvc.net"),
           apps=("tv.twitch.android.app",)),
    Option("video_sites", "video", "אתרי וידאו נוספים", "Vimeo, Dailymotion, Rumble, Bilibili.",
           hosts=("vimeo.com", "vimeocdn.com", "dailymotion.com", "dmcdn.net", "rumble.com", "bilibili.com",
                  "bilibili.tv"),
           apps=("com.vimeo.android.videoapp", "com.dailymotion.dailymotion", "com.rumble.battles")),
    Option("spotify", "video", "ספוטיפיי", "",
           hosts=("spotify.com", "scdn.co", "spotifycdn.com", "spotifycdn.net", "spoti.fi"),
           apps=("com.spotify.music",)),

    # ------------------------------------------------------------ קניות
    Option("aliexpress", "shopping", "אליאקספרס", "",
           hosts=("aliexpress.com", "aliexpress.us", "aliexpress.ru", "aliexpress-media.com", "alicdn.com"),
           apps=("com.alibaba.aliexpresshd",), popular=True),
    Option("temu", "shopping", "טמו", "", hosts=("temu.com", "kwcdn.com", "temucdn.com"),
           apps=("com.einnovation.temu",), popular=True),
    Option("shein", "shopping", "שיין", "", hosts=("shein.com", "shein.co.il", "ltwebstatic.com"),
           apps=("com.zzkko",), popular=True),
    Option("amazon", "shopping", "אמזון", "",
           hosts=("amazon.com", "amazon.co.uk", "amazon.de", "media-amazon.com", "ssl-images-amazon.com"),
           apps=("com.amazon.mShop.android.shopping",)),
    Option("ebay", "shopping", "איביי", "", hosts=("ebay.com", "ebayimg.com", "ebaystatic.com"),
           apps=("com.ebay.mobile",)),

    # ------------------------------------------------------------ חדשות וספורט
    Option("news_general", "news", "אתרי חדשות כלליים",
           "ynet, וואלה, מאקו, N12, מעריב, הארץ, ישראל היום, כאן ועוד.",
           hosts=("ynet.co.il", "walla.co.il", "mako.co.il", "n12.co.il", "maariv.co.il", "haaretz.co.il",
                  "israelhayom.co.il", "themarker.com", "calcalist.co.il", "globes.co.il", "kan.org.il",
                  "13tv.co.il", "reshet.tv", "i24news.tv", "0404.co.il", "ice.co.il")),
    Option("news_sport", "news", "אתרי ספורט", "ספורט 5, ONE, ספורט 1 ועוד.",
           hosts=("sport5.co.il", "one.co.il", "sport1.maariv.co.il", "vsport.co.il")),

    # ------------------------------------------------------------ בינה מלאכותית
    Option("ai_chat", "ai", "צ'אטבוטים ומחוללי תמונות",
           "ChatGPT, Gemini, Copilot, Perplexity, Character.AI, Grok, DeepSeek ועוד.",
           hosts=("chatgpt.com", "openai.com", "oaistatic.com", "oaiusercontent.com", "gemini.google.com",
                  "bard.google.com", "copilot.microsoft.com", "perplexity.ai", "character.ai", "poe.com",
                  "grok.com", "x.ai", "deepseek.com", "meta.ai", "claude.ai", "midjourney.com",
                  "leonardo.ai", "pi.ai"),
           apps=("com.openai.chatgpt", "com.google.android.apps.bard", "com.microsoft.copilot",
                 "ai.perplexity.app.android", "ai.character.app", "ai.x.grok", "com.deepseek.chat",
                 "com.anthropic.claude", "com.quora.poe")),

    # ------------------------------------------------------------ משחקים
    Option("games", "games", "משחקים אונליין",
           "Roblox, Fortnite, Minecraft, Clash, Free Fire, PUBG ועוד. נחסמים גם בטלפון עצמו.",
           hosts=("roblox.com", "rbxcdn.com", "epicgames.com", "fortnite.com", "steampowered.com",
                  "steamcommunity.com", "steamstatic.com", "minecraft.net", "supercell.com", "miniclip.com",
                  "poki.com", "crazygames.com", "y8.com", "friv.com"),
           apps=("com.roblox.client", "com.epicgames.fortnite", "com.mojang.minecraftpe",
                 "com.supercell.clashofclans", "com.supercell.clashroyale", "com.supercell.brawlstars",
                 "com.dts.freefireth", "com.dts.freefiremax", "com.tencent.ig", "com.pubg.imobile",
                 "com.activision.callofduty.shooter", "com.kiloo.subwaysurf", "com.king.candycrushsaga",
                 "com.miniclip.eightballpool", "com.garena.game.codm", "com.innersloth.spacemafia"),
           needs_guard=True),

    # ------------------------------------------------------------ היכרויות
    Option("dating", "dating", "אפליקציות היכרויות", "",
           hosts=("tinder.com", "gotinder.com", "bumble.com", "okcupid.com", "badoo.com", "hinge.co",
                  "match.com", "pof.com", "happn.com"),
           apps=("com.tinder", "com.bumble.app", "com.okcupid.okcupid", "com.badoo.mobile", "co.hinge.app",
                 "com.pof.android", "com.ftw_and_co.happn")),

    # ------------------------------------------------------------ הגנה מפני עקיפה
    Option("apk_sites", "protection", "אתרי הורדת אפליקציות (APK)",
           "APKPure, APKMirror, Aptoide, Uptodown, F-Droid — כדי שלא יתקינו אפליקציות בעקיפת החנות.",
           hosts=("apkpure.com", "apkpure.net", "apkmirror.com", "aptoide.com", "uptodown.com", "apkcombo.com",
                  "f-droid.org", "happymod.com", "an1.com", "apk.support", "apkmody.io", "liteapks.com"),
           apps=("com.apkpure.aegon", "cm.aptoide.pt", "com.uptodown", "org.fdroid.fdroid"),
           popular=True),
    Option("vpn_apps", "protection", "אפליקציות VPN ועקיפת סינון",
           "אנדרואיד מאפשר VPN אחד בכל רגע — VPN אחר מנתק את הסינון. חוסם את האפליקציות הנפוצות "
           "(השירותים עצמם כבר חסומים ב-DNS לכולם).",
           apps=("com.psiphon3", "com.psiphon3.subscription", "org.torproject.android", "com.nordvpn.android",
                 "com.expressvpn.vpn", "hotspotshield.android.vpn", "free.vpn.unblock.proxy.turbovpn",
                 "ch.protonvpn.android", "com.cloudflare.onedotonedotonedotone", "com.windscribe.vpn",
                 "com.surfshark.vpnclient.android", "de.blinkt.openvpn", "net.openvpn.openvpn",
                 "com.wireguard.android", "org.strongswan.android", "com.tunnelbear.android",
                 "com.privateinternetaccess.android", "free.vpn.unblock.proxy.vpnmaster", "com.v2ray.ang",
                 "com.github.shadowsocks", "io.nekohasekai.sfa", "com.getsurfboard"),
           popular=True, needs_guard=True),
)

CATALOG: dict[str, Option] = {o.id: o for o in OPTIONS}
assert len(CATALOG) == len(OPTIONS), "duplicate option id"
assert {o.category for o in OPTIONS} <= {c["id"] for c in CATEGORIES}

APP_IMAGES = ("auto", "block", "allow")
_HOST_RE = re.compile(r"^(?:\*\.)?(?:[a-z0-9*](?:[a-z0-9*-]{0,61}[a-z0-9*])?\.)+[a-z*]{2,63}$")
_PKG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")


def public_catalog() -> dict:
    """לטופס ההרשמה באפליקציה."""
    return {"levels": [{"level": k, **v} for k, v in LEVELS.items() if k > 0],
            "categories": CATEGORIES,
            "options": [o.public() for o in OPTIONS]}


def admin_catalog() -> dict:
    return {"levels": [{"level": k, **v} for k, v in LEVELS.items()],
            "categories": CATEGORIES,
            "options": [o.admin() for o in OPTIONS]}


def clean_host(raw: str) -> str | None:
    """'https://www.Example.com/path' → 'example.com'. None אם לא נראה כמו שם אתר."""
    h = raw.strip().lower()
    h = re.sub(r"^[a-z]+://", "", h).split("/", 1)[0].split(":", 1)[0].strip(".")
    if h.startswith("www."):
        h = h[4:]
    return h if _HOST_RE.match(h) and len(h) <= 253 else None


def clean_package(raw: str) -> str | None:
    p = raw.strip()
    return p if _PKG_RE.match(p) and len(p) <= 200 else None


# ---------------------------------------------------------------- מדיניות מכשיר

@dataclass(slots=True)
class DeviceSettings:
    """מה שנשמר לכל מכשיר (עמודת policy במסד, JSON)."""
    options: list[str] = field(default_factory=list)
    custom_hosts: list[str] = field(default_factory=list)
    allow_hosts: list[str] = field(default_factory=list)
    custom_apps: list[str] = field(default_factory=list)
    app_images: str = "auto"                    # auto = לפי הרמה (1–2 חסום, 3 מותר)

    @classmethod
    def load(cls, raw: str | None) -> DeviceSettings:
        try:
            d = json.loads(raw or "{}")
        except ValueError:
            d = {}
        return cls(
            options=[o for o in d.get("options", []) if o in CATALOG],
            custom_hosts=[h for h in map(clean_host, d.get("custom_hosts", [])) if h],
            allow_hosts=[h for h in map(clean_host, d.get("allow_hosts", [])) if h],
            custom_apps=[p for p in map(clean_package, d.get("custom_apps", [])) if p],
            app_images=d.get("app_images") if d.get("app_images") in APP_IMAGES else "auto",
        )

    def dump(self) -> str:
        return json.dumps({"options": self.options, "custom_hosts": self.custom_hosts,
                           "allow_hosts": self.allow_hosts, "custom_apps": self.custom_apps,
                           "app_images": self.app_images}, ensure_ascii=False, separators=(",", ":"))

    def as_dict(self) -> dict:
        return json.loads(self.dump())

    def update(self, changes: dict) -> list[str]:
        """מעדכן מ-PATCH של המנהל. מחזיר רשימת שגיאות (ריקה = הכול תקין)."""
        errors: list[str] = []
        if "options" in changes and changes["options"] is not None:
            unknown = [o for o in changes["options"] if o not in CATALOG]
            if unknown:
                errors.append(f"אפשרויות לא מוכרות: {', '.join(unknown)}")
            self.options = sorted({o for o in changes["options"] if o in CATALOG})
        for key, cleaner, label in (("custom_hosts", clean_host, "אתר"), ("allow_hosts", clean_host, "אתר"),
                                    ("custom_apps", clean_package, "שם חבילה")):
            if key in changes and changes[key] is not None:
                raw = [x for x in changes[key] if str(x).strip()]
                good = [cleaner(str(x)) for x in raw]
                bad = [str(x) for x, g in zip(raw, good, strict=True) if g is None]
                if bad:
                    errors.append(f"{label} לא תקין: {', '.join(bad[:5])}")
                setattr(self, key, sorted({g for g in good if g})[:500])
        if changes.get("app_images") is not None:
            if changes["app_images"] not in APP_IMAGES:
                errors.append("ערך לא חוקי לתמונות באפליקציות")
            else:
                self.app_images = changes["app_images"]
        return errors


def _covered_by(host: str, allow: list[str]) -> bool:
    return any(host == a or host.endswith("." + a) for a in allow)


@dataclass(frozen=True, slots=True)
class Compiled:
    hosts: tuple[str, ...]
    apps: tuple[str, ...]
    screens: tuple[dict, ...]
    paths: tuple[dict, ...]
    images: str
    video: bool
    app_images: str

    def proxy_json(self, level: int) -> str:
        """מה שהפרוקסי קורא (fp:policy ב-Redis)."""
        return json.dumps({"level": level, "hosts": list(self.hosts), "paths": list(self.paths),
                           "images": self.images, "video": self.video, "app_images": self.app_images},
                          separators=(",", ":"))

    def device_json(self) -> dict:
        """מה שהאפליקציה בטלפון אוכפת (מגן FilterPhone)."""
        return {"blocked_apps": list(self.apps), "blocked_screens": list(self.screens)}


def compile_settings(level: int, s: DeviceSettings) -> Compiled:
    opts = [CATALOG[o] for o in s.options if o in CATALOG]
    hosts = {h for o in opts for h in o.hosts} | set(s.custom_hosts)
    hosts = {h for h in hosts if not _covered_by(h.removeprefix("*."), s.allow_hosts)}
    screens: dict[str, set[str]] = {}
    for o in opts:
        for pkg, match in o.screens:
            screens.setdefault(pkg, set()).update(match)
    app_images = s.app_images
    if app_images == "auto":
        app_images = "block" if level in (1, 2) else "allow"
    return Compiled(
        hosts=tuple(sorted(hosts)),
        apps=tuple(sorted({a for o in opts for a in o.apps} | set(s.custom_apps))),
        screens=tuple({"package": p, "match": sorted(m)} for p, m in sorted(screens.items())),
        paths=tuple({"host": h, "re": r} for o in opts for h, r in o.paths),
        images="block" if any(o.images == "block" for o in opts) else "filter",
        video=any(o.video for o in opts),
        app_images=app_images,
    )


def summary(s: DeviceSettings) -> list[dict]:
    """רשימת החסימות הפעילות — לתצוגה (בממשק הניהול ובאפליקציה)."""
    out = [{"id": o, "title": CATALOG[o].title, "category": CATALOG[o].category} for o in s.options]
    if s.custom_hosts:
        out.append({"id": "custom_hosts", "title": f"אתרים נוספים ({len(s.custom_hosts)})", "category": "custom"})
    if s.custom_apps:
        out.append({"id": "custom_apps", "title": f"אפליקציות נוספות ({len(s.custom_apps)})", "category": "custom"})
    return out
