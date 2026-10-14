"""קטלוג אפשרויות החסימה — מקור אחד לממשק הניהול, לטופס ההרשמה באפליקציה ולאכיפה.

לכל אפשרות שלוש דרכי אכיפה, והיא משתמשת בכל מה שרלוונטי לה:
  hosts   — חסימה ברשת: DNS (AdGuard, לפי כתובת המכשיר) + הפרוקסי (SNI / Host).
            עובד על כל אפליקציה ודפדפן, בלי שום התקנה נוספת בטלפון.
  apps    — חסימה בתוך הטלפון: "מגן FilterPhone" (נגישות / מנהל מכשיר) לא נותן לפתוח
            את האפליקציה. נחוץ כשאי אפשר להבדיל ברשת (דפדפנים, טלגרם, משחקים).
  screens — מסך מסוים בתוך אפליקציה (למשל צפייה בסטטוסים בוואטסאפ).
  protections — הגנה מיוחדת בטלפון (למשל זיהוי צפייה בתמונת פרופיל בוואטסאפ — PROTECTIONS).
ועוד דגלים לפרוקסי: images=block (בלי תמונות), video (בלי וידאו), paths (כתובות מסוימות).

כתובת 'example.com' חוסמת גם כל תת-דומיין. '*' = כל רצף (למשל media*.whatsapp.net).

בנוסף לאפשרויות — חסימה של כל אפליקציה שמותקנת בטלפון, בשלושה מצבים (app_rules):
  network       — השרתים של האפליקציה נחסמים ברשת (מוכרים מהקטלוג, או שהמנהל הוסיף);
                  האפליקציה נפתחת אבל לא מתחברת.
  accessibility — האפליקציה לא נפתחת בטלפון (מגן FilterPhone / מנהל מכשיר); הרשת לא משתנה.
  both          — שניהם.
"""
from __future__ import annotations

import hashlib
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
    protections: tuple[str, ...] = ()           # מפתחות ב-PROTECTIONS — הגנות מיוחדות בטלפון

    @property
    def mechanisms(self) -> list[str]:
        m = []
        if self.hosts or self.paths:
            m.append("network")
        if self.apps or self.screens or self.protections:
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
                "protections": list(self.protections), "images": self.images, "video": self.video}


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
           "תמונות הפרופיל של אנשי הקשר והקבוצות לא נטענות, ואי אפשר לפתוח תמונת פרופיל (גם כזו שכבר "
           "שמורה בטלפון). הודעות, שיחות, קבצים ותמונות בצ'אט — כרגיל.",
           hosts=("pps.whatsapp.net",), protections=("wa_profile_photo",), popular=True, needs_guard=True,
           warn="פתיחת תמונת פרופיל נחסמת בטלפון עצמו (מגן FilterPhone). התמונות הקטנות ברשימת הצ'אטים "
                "שכבר שמורות בטלפון עשויות להמשיך להופיע עד שוואטסאפ מרענן אותן."),
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
    # Play חסום רק *לכניסה* (מגן FilterPhone מחזיר החוצה) — לא מוסתר במנהל המכשיר ולא חסום ברשת, כדי
    # שעדכוני אפליקציות ברקע (וואטסאפ, ביט...) וההתקנה דרך חנות FilterPhone ימשיכו לעבוד.
    Option("play_store", "google", "כניסה לחנות Google Play",
           "אי אפשר להיכנס לחנות ולהתקין ממנה ישירות. אפליקציות מאושרות מותקנות ומתעדכנות דרך חנות FilterPhone.",
           apps=("com.android.vending",), popular=True, needs_guard=True,
           warn="נאכף ע\"י מגן FilterPhone (נגישות). מומלץ לחסום יחד גם 'אתרי הורדת APK'."),
    Option("play_store_full", "google", "Google Play — חסימה מלאה",
           "החנות חסומה גם ברשת ומוסתרת: אין התקנות ואין עדכוני אפליקציות בכלל.",
           hosts=("play.google.com", "play.googleapis.com", "play-fe.googleapis.com",
                  "play-lh.googleusercontent.com", "market.android.com"),
           apps=("com.android.vending",),
           warn="גם עדכוני אבטחה של וואטסאפ וכו' וההתקנה דרך חנות FilterPhone ייעצרו."),
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
           "האפליקציה עצמה — החיפוש והפיד. ג'מיני, Circle to Search, Lens והעוזר ממשיכים לעבוד (הם רצים "
           "מתוך אותה אפליקציה), והחיפוש בכרום לא מושפע.",
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
                 "com.vanced.android.youtube"), popular=True,
           warn="FilterTube לא מושפעת: היא עובדת מחוץ ל-VPN ומסננת בעצמה (ראו 'אפליקציות מחוץ ל-VPN')."),
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
    Option("galaxy_store", "protection", "חנות Galaxy (Samsung)",
           "חנות האפליקציות של סמסונג — כדי שלא יתקינו ממנה אפליקציות בעקיפת החנות שלכם.",
           apps=("com.sec.android.app.samsungapps", "com.samsung.android.app.galaxyfinder"),
           popular=True, needs_guard=True),
)

CATALOG: dict[str, Option] = {o.id: o for o in OPTIONS}
assert len(CATALOG) == len(OPTIONS), "duplicate option id"

# ברירת המחדל ללקוח חדש (קוד הפעלה / הרשמה): מצב מחמיר, רשתות חברתיות, בלי דפדפן וטלגרם,
# הסתרת יוטיוב (כולל YouTube Music) וחנויות האפליקציות, ומניעת עקיפה. המנהל יכול לשנות בכל רגע.
DEFAULT_LEVEL = 1       # מחמיר
DEFAULT_OPTIONS: tuple[str, ...] = (
    *(o.id for o in OPTIONS if o.category == "social"),      # כל הרשתות החברתיות
    "browsers_all",                                          # בלי דפדפן בכלל (כולל Chrome)
    "telegram",
    "youtube",                                               # כולל YouTube Music
    "play_store", "galaxy_store",                            # חנויות — חסומות לכניסה
    "apk_sites", "vpn_apps",                                 # מניעת עקיפת הסינון
)
assert all(o in CATALOG for o in DEFAULT_OPTIONS), "unknown default option"
assert {o.category for o in OPTIONS} <= {c["id"] for c in CATEGORIES}

# שרתים מוכרים לכל אפליקציה (מהקטלוג) — לחסימת אפליקציה "ברשת" גם כשהיא נבחרה בנפרד
KNOWN_APP_HOSTS: dict[str, tuple[str, ...]] = {}
for _o in OPTIONS:
    if _o.hosts and _o.apps and not _o.id.startswith("browsers"):
        for _pkg in _o.apps:
            KNOWN_APP_HOSTS[_pkg] = tuple(sorted({*KNOWN_APP_HOSTS.get(_pkg, ()), *_o.hosts}))

APP_MODES = ("network", "accessibility", "both")
DEVICE_MODES = ("accessibility", "both")       # נאכף בטלפון
NETWORK_MODES = ("network", "both")            # נאכף בשרת

# חבילה אחת שמארחת כמה שירותים: חסימה שלה (באפשרות או בכרטיס האפליקציות) חוסמת רק את המסכים של
# האפליקציה עצמה, ולא מוסתרת/מושבתת במנהל מכשיר — שאר השירותים תלויים בה. אפליקציית גוגל: החיפוש,
# הפיד ושורת החיפוש חסומים; ג'מיני, Circle to Search, Lens והעוזר (שרצים מתוכה) — עובדים.
# התבניות = חלק משם המסך (Activity), בלי תלות באותיות גדולות. מסך לא מוכר לא נחסם.
SPLIT_APPS: dict[str, tuple[str, ...]] = {
    "com.google.android.googlequicksearchbox": (
        "googleappactivity",                    # המסך הראשי (גרסאות 2023 ואילך)
        "googlequicksearchbox.searchactivity",  # כניסה מהאייקון
        "searchnowactivity",                    # הפיד והחיפוש (גרסאות קודמות)
        "velvetactivity",                       # תוצאות חיפוש (גרסאות ישנות)
        "queryentryactivity",                   # שורת החיפוש / הווידג'ט
        "searchresultsactivity",
    ),
}

# אפליקציות שנחסמות רק בנגישות (כניסה), ולא מוסתרות/מושבתות במנהל המכשיר — כי הן צריכות להמשיך לרוץ
# ברקע. אם נבחרה האפשרות ה"מלאה" שלהן — מוסתרות כרגיל.
ACCESSIBILITY_ONLY_APPS: dict[str, str] = {"com.android.vending": "play_store_full"}

# הגנות מיוחדות בטלפון. ההגדרות (תבניות זיהוי) נשלחות מהשרת — כשוואטסאפ משנה את המסכים שלו,
# מעדכנים כאן בלי לשחרר אפליקציה חדשה. האפליקציה מחזיקה גם ברירת מחדל זהה.
PROTECTIONS: dict[str, dict] = {
    "wa_profile_photo": {
        "packages": ["com.whatsapp", "com.whatsapp.w4b"],
        # שכבה 1: שם המסך (Activity / חלון) — מכיל אחד מאלה (בלי תלות באותיות גדולות)
        "activities": ["viewprofilephoto", "profilephoto", "profilepicture", "quickcontact", "photo.viewprofile"],
        # שכבה 2: מזהי רכיבים (view id) של תמונת פרופיל גדולה
        # (בלי מזהים כלליים כמו "picture" — כדי לא לחסום בטעות צפייה בתמונות בצ'אט)
        "view_ids": ["profile_picture", "profile_photo", "contact_photo", "quick_contact_photo",
                     "wds_profile_photo", "profile_picture_image"],
        # מסכים שלעולם לא נחשבים צפייה בתמונת פרופיל (צפייה במדיה של הצ'אט, גלריה, מצלמה)
        "exclude_windows": ["mediaview", "gallery", "camera", "status"],
        # שכבה 3: תיאור נגישות של התמונה (רק ברכיב תמונה; בלי לקרוא טקסט אחר)
        "descriptions": ["profile photo", "profile picture", "תמונת פרופיל", "תמונת הפרופיל", "foto de perfil",
                         "photo de profil", "profilbild", "фото профиля", "صورة الملف الشخصي"],
        # תמונה שתופסת לפחות חלק כזה מהמסך = מצב צפייה (ולא אייקון קטן ברשימה)
        "min_area": 0.3,
    },
}

# מנהל מכשיר (Device Owner) — מה נאכף כשהאפליקציה מוגדרת כמנהלת המכשיר. רק יכולות רשמיות
# של DevicePolicyManager. במכשיר שאינו Device Owner — אין לזה השפעה (ומוצג ככזה בממשק).
DPC_DEFAULTS: dict[str, bool | list] = {
    "block_uninstall": True,            # אי אפשר להסיר את FilterPhone
    "always_on_vpn": True,              # VPN תמיד מופעל + חסימת חיבורים בלי VPN (lockdown)
    "disallow_config_vpn": True,        # אי אפשר לשנות הגדרות VPN
    "disallow_factory_reset": True,     # איפוס להגדרות יצרן מתוך ההגדרות חסום (לא מצב שחזור!)
    "disallow_safe_boot": True,         # מצב בטוח (שעוקף אפליקציות) חסום
    "disallow_add_user": True,          # אי אפשר להוסיף משתמש / אורח
    "disallow_unknown_sources": True,   # אי אפשר להתקין קבצי APK
    "disallow_private_dns": True,       # אי אפשר להגדיר DNS פרטי (עוקף)
    "disallow_debugging": False,        # ניפוי באגים (ADB) — כבוי כברירת מחדל כדי שאפשר יהיה לתקן
    "disallow_install_apps": False,     # אי אפשר להתקין אפליקציות בכלל (גם מהחנות)
    "disallow_modify_accounts": False,  # אי אפשר להוסיף / להסיר חשבונות
    "hide_blocked_apps": True,          # אפליקציות חסומות מוסתרות לגמרי (במקום חסימה בנגישות)
    # אפליקציות חסומות "באפור": האייקון נשאר, לחיצה מציגה "חסום על ידי המנהל" (setPackagesSuspended).
    # כשפעיל — גובר על ההסתרה (גרסאות ישנות של האפליקציה לא מכירות אותו וממשיכות להסתיר).
    "suspend_blocked_apps": True,
    "disallow_tethering": True,         # נקודה חמה / שיתוף אינטרנט (Wi-Fi, בלוטות', USB) — חסום ואפור
    "frp_accounts": [],                 # Factory Reset Protection: מזהי חשבון Google שיכולים לשחרר
    "lock_task_packages": [],           # אפליקציות שמותר להן מצב קיוסק (lock task)
}
_FRP_ID = re.compile(r"^\d{6,30}$")

# חסימת מילות חיפוש דרך הנגישות: אם הלקוח מקליד/מחפש מילה מהרשימה בכל אפליקציה, המגן מחזיר אותו
# אחורה. פעיל כברירת מחדל, עם רשימת בסיס רב-לשונית שהמנהל יכול להרחיב או לצמצם לכל מכשיר.
# זו רשימת חסימה (denylist) של מונחי תוכן מפורש בלבד — הטלפון משווה אליה טקסט מקומית ולא שומר/שולח
# כלום. הרשימה מנורמלת (אותיות קטנות, בלי ניקוד/טעמים) — ראו clean_term והזיהוי באנדרואיד.
_KEYWORD_SEED: tuple[str, ...] = (
    # עברית
    "סקס", "סקסי", "פורנו", "פורנוגרפיה", "פורנהאב", "עירום", "תמונות עירום", "זיון", "זונה",
    "חשפנית", "בקיני", "ביקיני", "אתרי סקס",
    # English
    "sex", "sexy", "porn", "porno", "pornography", "pornhub", "xvideos", "xnxx", "redtube",
    "youporn", "onlyfans", "hentai", "xxx", "nude", "nudes", "naked", "boobs", "milf", "escort",
    "camgirl", "lingerie", "bikini", "nsfw",
    # العربية / Русский / Français / Español
    "سكس", "اباحي", "عاري", "секс", "порно", "голый", "sexe", "porno", "desnuda", "sexo",
    # 中文 / 日本語 / 한국어
    "性交", "色情", "裸体", "エロ", "アダルト", "포르노", "섹스",
)
KEYWORD_DEFAULTS: dict = {"enabled": True, "action": "back"}
MAX_KEYWORD_TERMS = 1000
_TERM_STRIP = re.compile(r"\s+")

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
            "options": [o.admin() for o in OPTIONS],
            "defaults": {"level": DEFAULT_LEVEL, "options": list(DEFAULT_OPTIONS)}}


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


def clean_term(raw: str) -> str | None:
    """מילת חיפוש לחסימה: אותיות קטנות, בלי רווחים כפולים, עד 40 תווים. None אם ריק."""
    t = _TERM_STRIP.sub(" ", str(raw).strip().lower())[:40].strip()
    return t or None


def resolve_keywords(cfg: dict | None) -> dict:
    """ההגדרה האפקטיבית שנשלחת לטלפון: {enabled, action, terms} — ברירת המחדל + תוספות המנהל
    פחות מה שהוסר. terms מנורמל וממוין (השוואה בטלפון היא מקומית, בלי לשמור טקסט)."""
    cfg = cfg if isinstance(cfg, dict) else {}
    enabled = cfg.get("enabled", KEYWORD_DEFAULTS["enabled"])
    removed = {t for t in map(clean_term, cfg.get("removed") or []) if t}
    extra = {t for t in map(clean_term, cfg.get("extra") or []) if t}
    base = {t for t in map(clean_term, _KEYWORD_SEED) if t}
    terms = sorted((base | extra) - removed)[:MAX_KEYWORD_TERMS]
    return {"enabled": bool(enabled), "action": "back", "terms": terms}


# אפליקציות מחוץ ל-VPN (פיצול מנהרה לפי אפליקציה): עוברות בחיבור הרגיל של הטלפון ולא דרך השרת.
# FilterTube — שתי הגרסאות (הראשית, NewPipe, ו"גרסת החנות", Flutter) — מתחברת ליוטיוב ישירות מהטלפון,
# ויוטיוב מסרב לבקשות שמגיעות מכתובת של שרת ("תאשר שאתה לא בוט"). מחוץ ל-VPN היא עובדת כרגיל.
# המחיר: אפליקציה מוחרגת לא עוברת שום סינון של FilterPhone (גם לא DNS). לכן רק אפליקציות שמסננות
# בעצמן, לפי שם חבילה מדויק, ואפשר לנעול לחתימה (SHA-256 של תעודת החתימה) — אז אפליקציה אחרת
# עם אותו שם חבילה לא תוחרג. דפדפנים וחנויות לעולם לא מוחרגים.
# הטלפון מחזיק ברירת מחדל זהה (VpnBypass.DEFAULTS) לשרת שעוד לא שולח את השדה.
VPN_BYPASS_APPS: dict[str, str] = {
    "com.filtertube.app": "FilterTube",
    "com.filtertube.filtertube": "FilterTube — גרסת החנות",
}
VPN_BYPASS_DEFAULTS: dict = {"enabled": True}
MAX_VPN_BYPASS = 20
MAX_BYPASS_PINS = 3
NEVER_BYPASS = frozenset({
    "com.filterphone.app", "com.android.chrome", *BROWSERS_OTHER, "com.android.vending",
    "com.google.android.googlequicksearchbox", "com.google.android.gms", "com.android.settings",
})
_CERT_RE = re.compile(r"^[0-9a-f]{64}$")


def clean_cert(raw) -> str | None:
    """טביעת SHA-256 של תעודת חתימה: 64 תווי hex (מותר עם ':' ורווחים). None אם לא תקין."""
    c = re.sub(r"[\s:]", "", str(raw)).lower()
    return c if _CERT_RE.match(c) else None


def resolve_vpn_bypass(cfg: dict | None) -> dict:
    """ההגדרה האפקטיבית שנשלחת לטלפון: {enabled, apps: [{package, label, certs}]} — ברירת המחדל +
    תוספות המנהל פחות מה שהוסר. certs ריק = לפי שם חבילה בלבד; אחרת רק חתימה תואמת מוחרגת."""
    cfg = cfg if isinstance(cfg, dict) else {}
    if cfg.get("enabled", VPN_BYPASS_DEFAULTS["enabled"]) is False:
        return {"enabled": False, "apps": []}
    removed = {p for p in map(clean_package, map(str, cfg.get("removed") or [])) if p}
    extra = {p for p in map(clean_package, map(str, cfg.get("extra") or [])) if p}
    pins = cfg.get("pins") if isinstance(cfg.get("pins"), dict) else {}
    pkgs = sorted(((set(VPN_BYPASS_APPS) | extra) - removed) - NEVER_BYPASS)[:MAX_VPN_BYPASS]
    return {"enabled": True, "apps": [
        {"package": p, "label": VPN_BYPASS_APPS.get(p, ""),
         "certs": sorted({c for c in map(clean_cert, pins.get(p) or []) if c})[:MAX_BYPASS_PINS]}
        for p in pkgs]}


# ---------------------------------------------------------------- מדיניות מכשיר

@dataclass(slots=True)
class DeviceSettings:
    """מה שנשמר לכל מכשיר (עמודת policy במסד, JSON)."""
    options: list[str] = field(default_factory=list)
    custom_hosts: list[str] = field(default_factory=list)
    allow_hosts: list[str] = field(default_factory=list)
    app_rules: dict[str, dict] = field(default_factory=dict)    # pkg → {"mode": ..., "hosts": [...]}
    app_images: str = "auto"                    # auto = לפי הרמה (1–2 חסום, 3 מותר)
    dpc: dict = field(default_factory=dict)     # רק מה ששונה מ-DPC_DEFAULTS
    keywords: dict = field(default_factory=dict)  # חסימת מילות חיפוש: {enabled, extra, removed} מעל ברירת המחדל
    vpn_bypass: dict = field(default_factory=dict)  # אפליקציות מחוץ ל-VPN: {enabled, extra, removed, pins}

    @property
    def custom_apps(self) -> list[str]:
        """תאימות לאחור: האפליקציות שנחסמות בטלפון (כמו הרשימה הישנה)."""
        return sorted(p for p, r in self.app_rules.items() if r["mode"] in DEVICE_MODES)

    @staticmethod
    def _clean_rule(mode, hosts) -> dict | None:
        if mode not in APP_MODES:
            return None
        rule: dict = {"mode": mode}
        hosts = sorted({h for h in map(clean_host, hosts or []) if h})[:50]
        if hosts:
            rule["hosts"] = hosts
        return rule

    @classmethod
    def _load_rules(cls, d: dict) -> dict[str, dict]:
        rules: dict[str, dict] = {}
        for p in map(clean_package, d.get("custom_apps", []) or []):     # פורמט ישן: חסימה בטלפון
            if p:
                rules[p] = {"mode": "accessibility"}
        raw = d.get("app_rules") or {}
        if isinstance(raw, list):                                         # [{"package", "mode", "hosts"}]
            raw = {str(r.get("package", "")): r for r in raw if isinstance(r, dict)}
        if isinstance(raw, dict):
            for pkg, r in raw.items():
                p = clean_package(str(pkg))
                rule = cls._clean_rule(r.get("mode"), r.get("hosts")) if p and isinstance(r, dict) else None
                if rule:
                    rules[p] = rule
        return dict(sorted(rules.items())[:500])

    @staticmethod
    def _load_dpc(raw) -> dict:
        out: dict = {}
        if not isinstance(raw, dict):
            return out
        for k, default in DPC_DEFAULTS.items():
            if k not in raw:
                continue
            v = raw[k]
            if isinstance(default, bool) and isinstance(v, bool) and v != default:
                out[k] = v
            elif k == "frp_accounts" and isinstance(v, list):
                ids = sorted({str(x).strip() for x in v if _FRP_ID.match(str(x).strip())})[:10]
                if ids:
                    out[k] = ids
            elif k == "lock_task_packages" and isinstance(v, list):
                pk = sorted({p for p in map(clean_package, map(str, v)) if p})[:20]
                if pk:
                    out[k] = pk
        return out

    @staticmethod
    def _load_keywords(raw) -> dict:
        if not isinstance(raw, dict):
            return {}
        out: dict = {}
        if isinstance(raw.get("enabled"), bool) and raw["enabled"] != KEYWORD_DEFAULTS["enabled"]:
            out["enabled"] = raw["enabled"]
        for key in ("extra", "removed"):
            if isinstance(raw.get(key), list):
                vals = sorted({t for t in map(clean_term, raw[key]) if t})[:MAX_KEYWORD_TERMS]
                if vals:
                    out[key] = vals
        return out

    @staticmethod
    def _load_vpn_bypass(raw) -> dict:
        if not isinstance(raw, dict):
            return {}
        out: dict = {}
        if isinstance(raw.get("enabled"), bool) and raw["enabled"] != VPN_BYPASS_DEFAULTS["enabled"]:
            out["enabled"] = raw["enabled"]
        for key in ("extra", "removed"):
            if isinstance(raw.get(key), list):
                vals = sorted({p for p in map(clean_package, map(str, raw[key])) if p and p not in NEVER_BYPASS})
                if vals:
                    out[key] = vals[:MAX_VPN_BYPASS]
        pins: dict = {}
        if isinstance(raw.get("pins"), dict):
            for pkg, certs in list(raw["pins"].items())[:MAX_VPN_BYPASS]:
                p = clean_package(str(pkg))
                good = sorted({c for c in map(clean_cert, certs if isinstance(certs, list) else []) if c})
                if p and good:
                    pins[p] = good[:MAX_BYPASS_PINS]
        if pins:
            out["pins"] = dict(sorted(pins.items()))
        return out

    @classmethod
    def load(cls, raw: str | None) -> DeviceSettings:
        try:
            d = json.loads(raw or "{}")
        except ValueError:
            d = {}
        if not isinstance(d, dict):
            d = {}
        return cls(
            options=[o for o in d.get("options", []) if o in CATALOG],
            custom_hosts=[h for h in map(clean_host, d.get("custom_hosts", [])) if h],
            allow_hosts=[h for h in map(clean_host, d.get("allow_hosts", [])) if h],
            app_rules=cls._load_rules(d),
            app_images=d.get("app_images") if d.get("app_images") in APP_IMAGES else "auto",
            dpc=cls._load_dpc(d.get("dpc")),
            keywords=cls._load_keywords(d.get("keywords")),
            vpn_bypass=cls._load_vpn_bypass(d.get("vpn_bypass")),
        )

    def dump(self) -> str:
        # custom_apps נשמר גם הוא — כדי שגרסה קודמת של השרת (חזרה לאחור) תמשיך לחסום
        return json.dumps({"options": self.options, "custom_hosts": self.custom_hosts,
                           "allow_hosts": self.allow_hosts, "custom_apps": self.custom_apps,
                           "app_rules": self.app_rules, "app_images": self.app_images, "dpc": self.dpc,
                           "keywords": self.keywords, "vpn_bypass": self.vpn_bypass},
                          ensure_ascii=False, separators=(",", ":"))

    def as_dict(self) -> dict:
        d = json.loads(self.dump())
        d["dpc_effective"] = {**DPC_DEFAULTS, **self.dpc}
        d["keywords_effective"] = resolve_keywords(self.keywords)      # {enabled, action, terms}
        d["keyword_defaults_count"] = len(resolve_keywords({})["terms"])
        d["vpn_bypass_effective"] = resolve_vpn_bypass(self.vpn_bypass)  # {enabled, apps}
        d["vpn_bypass_defaults"] = [{"package": p, "label": t} for p, t in VPN_BYPASS_APPS.items()]
        return d

    def update(self, changes: dict) -> list[str]:
        """מעדכן מ-PATCH של המנהל. מחזיר רשימת שגיאות (ריקה = הכול תקין)."""
        errors: list[str] = []
        if "options" in changes and changes["options"] is not None:
            unknown = [o for o in changes["options"] if o not in CATALOG]
            if unknown:
                errors.append(f"אפשרויות לא מוכרות: {', '.join(unknown)}")
            self.options = sorted({o for o in changes["options"] if o in CATALOG})
        for key, cleaner, label in (("custom_hosts", clean_host, "אתר"), ("allow_hosts", clean_host, "אתר")):
            if key in changes and changes[key] is not None:
                raw = [x for x in changes[key] if str(x).strip()]
                good = [cleaner(str(x)) for x in raw]
                bad = [str(x) for x, g in zip(raw, good, strict=True) if g is None]
                if bad:
                    errors.append(f"{label} לא תקין: {', '.join(bad[:5])}")
                setattr(self, key, sorted({g for g in good if g})[:500])
        if changes.get("custom_apps") is not None:
            # הפורמט הישן: הרשימה המלאה של האפליקציות שנחסמות בטלפון
            raw = [str(x) for x in changes["custom_apps"] if str(x).strip()]
            good = [clean_package(x) for x in raw]
            bad = [x for x, g in zip(raw, good, strict=True) if g is None]
            if bad:
                errors.append(f"שם חבילה לא תקין: {', '.join(bad[:5])}")
            wanted = {g for g in good if g}
            rules = {p: r for p, r in self.app_rules.items() if r["mode"] == "network" or p in wanted}
            for p in wanted:
                if p not in rules or rules[p]["mode"] == "network":
                    rules[p] = {**rules.get(p, {}), "mode": "both" if p in rules else "accessibility"}
            self.app_rules = dict(sorted(rules.items())[:500])
        if changes.get("app_rules") is not None:
            raw = changes["app_rules"]
            items = raw.items() if isinstance(raw, dict) else (
                (r.get("package"), r) for r in raw if isinstance(r, dict))
            rules: dict[str, dict] = {}
            for pkg, r in items:
                p = clean_package(str(pkg or ""))
                rule = self._clean_rule((r or {}).get("mode"), (r or {}).get("hosts")) if p else None
                if rule is None:
                    errors.append(f"כלל אפליקציה לא תקין: {pkg}")
                    continue
                rules[p] = rule
            self.app_rules = dict(sorted(rules.items())[:500])
        if changes.get("app_images") is not None:
            if changes["app_images"] not in APP_IMAGES:
                errors.append("ערך לא חוקי לתמונות באפליקציות")
            else:
                self.app_images = changes["app_images"]
        if changes.get("dpc") is not None:
            if not isinstance(changes["dpc"], dict):
                errors.append("הגדרות מנהל מכשיר לא תקינות")
            else:
                unknown = [k for k in changes["dpc"] if k not in DPC_DEFAULTS]
                if unknown:
                    errors.append(f"הגדרות מנהל מכשיר לא מוכרות: {', '.join(unknown)}")
                bad_frp = [x for x in changes["dpc"].get("frp_accounts") or [] if not _FRP_ID.match(str(x).strip())]
                if bad_frp:
                    errors.append("מזהה חשבון Google לשחרור FRP צריך להיות מספר (לא כתובת מייל)")
                self.dpc = self._load_dpc({**self.dpc, **changes["dpc"]})
        if changes.get("keywords") is not None:
            kw = changes["keywords"]
            if not isinstance(kw, dict):
                errors.append("הגדרות חסימת מילים לא תקינות")
            else:
                merged = dict(self.keywords)
                if isinstance(kw.get("enabled"), bool):
                    merged["enabled"] = kw["enabled"]
                for key in ("extra", "removed"):
                    if kw.get(key) is not None:
                        if not isinstance(kw[key], list):
                            errors.append(f"רשימת מילים לא תקינה ({key})")
                        else:
                            merged[key] = [str(x) for x in kw[key]]
                self.keywords = self._load_keywords(merged)
        if changes.get("vpn_bypass") is not None:
            errors += self._update_vpn_bypass(changes["vpn_bypass"])
        return errors

    def _update_vpn_bypass(self, vb) -> list[str]:
        """{enabled?, extra?, removed?, pins?: {pkg: [sha256] | []}} — pins עם רשימה ריקה מבטל נעילה."""
        if not isinstance(vb, dict):
            return ["הגדרות 'מחוץ ל-VPN' לא תקינות"]
        errors: list[str] = []
        merged = {**self.vpn_bypass, "pins": dict(self.vpn_bypass.get("pins") or {})}
        if isinstance(vb.get("enabled"), bool):
            merged["enabled"] = vb["enabled"]
        for key in ("extra", "removed"):
            if vb.get(key) is None:
                continue
            if not isinstance(vb[key], list):
                errors.append(f"רשימת אפליקציות לא תקינה ({key})")
                continue
            raw = [str(x).strip() for x in vb[key] if str(x).strip()]
            bad = [x for x in raw if clean_package(x) is None]
            if bad:
                errors.append(f"שם חבילה לא תקין: {', '.join(bad[:5])}")
            never = [x for x in raw if x in NEVER_BYPASS]
            if key == "extra" and never:
                errors.append(f"אי אפשר להוציא מהסינון דפדפן, חנות או רכיב מערכת: {', '.join(never[:5])}")
            merged[key] = raw
        if vb.get("pins") is not None:
            if not isinstance(vb["pins"], dict):
                errors.append("נעילת חתימה לא תקינה")
            else:
                for pkg, certs in vb["pins"].items():
                    if not isinstance(certs, list) or any(clean_cert(c) is None for c in certs):
                        errors.append(f"טביעת חתימה לא תקינה ל-{pkg} (צריך SHA-256 — 64 תווי hex)")
                        continue
                    if certs:
                        merged["pins"][str(pkg)] = certs
                    else:
                        merged["pins"].pop(str(pkg), None)
        if not errors:
            self.vpn_bypass = self._load_vpn_bypass(merged)
        return errors


def _covered_by(host: str, allow: list[str]) -> bool:
    return any(host == a or host.endswith("." + a) for a in allow)


@dataclass(frozen=True, slots=True)
class Compiled:
    hosts: tuple[str, ...]
    apps: tuple[str, ...]                       # נחסמות בטלפון (נגישות / מנהל מכשיר)
    screens: tuple[dict, ...]
    paths: tuple[dict, ...]
    images: str
    video: bool
    app_images: str
    app_rules: tuple[dict, ...] = ()            # [{"package", "mode"}] — מה שהמנהל בחר לכל אפליקציה
    protections: tuple[str, ...] = ()
    dpc: dict = field(default_factory=dict)
    keywords: dict = field(default_factory=dict)  # {enabled, action, terms} — חסימת מילות חיפוש בנגישות
    vpn_bypass: dict = field(default_factory=dict)  # {enabled, apps} — אפליקציות שעוברות מחוץ ל-VPN
    network_only_unknown: tuple[str, ...] = ()  # אפליקציות "ברשת" בלי שרתים ידועים — אין מה לחסום
    split_apps: tuple[str, ...] = ()            # חסומות רק במסכים שלהן (SPLIT_APPS), לא כאפליקציה שלמה
    accessibility_only: tuple[str, ...] = ()    # חסומות בנגישות בלבד — לא מוסתרות במנהל המכשיר

    def proxy_json(self, level: int) -> str:
        """מה שהפרוקסי קורא (fp:policy ב-Redis)."""
        return json.dumps({"level": level, "hosts": list(self.hosts), "paths": list(self.paths),
                           "images": self.images, "video": self.video, "app_images": self.app_images},
                          separators=(",", ":"))

    @property
    def needs_accessibility(self) -> bool:
        """מסכים, הגנות מיוחדות וחסימת מילים — רק בנגישות. חסימת אפליקציות — גם מנהל מכשיר (הסתרה)."""
        return bool(self.screens or self.protections or self.keywords.get("enabled"))

    def device_json(self) -> dict:
        """מה שהאפליקציה בטלפון אוכפת (מגן FilterPhone / מנהל מכשיר).

        blocked_apps נשאר רשימת מחרוזות — גרסאות ישנות של האפליקציה ממשיכות לחסום.
        שדות חדשים (app_rules, protections, dpc) — גרסאות ישנות פשוט מתעלמות."""
        return {
            "blocked_apps": list(self.apps),
            "blocked_screens": list(self.screens),
            "app_rules": list(self.app_rules),
            "protections": {k: PROTECTIONS[k] for k in self.protections if k in PROTECTIONS},
            "dpc": self.dpc,
            "keywords": self.keywords,
            "vpn_bypass": self.vpn_bypass,
            "accessibility_only": list(self.accessibility_only),
        }


def compile_settings(level: int, s: DeviceSettings) -> Compiled:
    opts = [CATALOG[o] for o in s.options if o in CATALOG]
    hosts = {h for o in opts for h in o.hosts} | set(s.custom_hosts)
    unknown: list[str] = []
    for pkg, rule in s.app_rules.items():
        if rule["mode"] in NETWORK_MODES:
            app_hosts = set(rule.get("hosts", ())) | set(KNOWN_APP_HOSTS.get(pkg, ()))
            hosts |= app_hosts
            if not app_hosts:
                unknown.append(pkg)
    hosts = {h for h in hosts if not _covered_by(h.removeprefix("*."), s.allow_hosts)}
    screens: dict[str, set[str]] = {}
    for o in opts:
        for pkg, match in o.screens:
            screens.setdefault(pkg, set()).update(match)
    app_images = s.app_images
    if app_images == "auto":
        app_images = "block" if level in (1, 2) else "allow"
    device_apps = {a for o in opts for a in o.apps} | {p for p, r in s.app_rules.items() if r["mode"] in DEVICE_MODES}
    split = sorted(device_apps & set(SPLIT_APPS))
    for pkg in split:                       # לא כאפליקציה שלמה — רק המסכים של האפליקציה עצמה
        device_apps.discard(pkg)
        screens.setdefault(pkg, set()).update(SPLIT_APPS[pkg])
    return Compiled(
        hosts=tuple(sorted(hosts)),
        apps=tuple(sorted(device_apps)),
        screens=tuple({"package": p, "match": sorted(m)} for p, m in sorted(screens.items())),
        paths=tuple({"host": h, "re": r} for o in opts for h, r in o.paths),
        images="block" if any(o.images == "block" for o in opts) else "filter",
        video=any(o.video for o in opts),
        app_images=app_images,
        app_rules=tuple({"package": p, "mode": r["mode"]} for p, r in s.app_rules.items()),
        protections=tuple(sorted({k for o in opts for k in o.protections})),
        dpc={**DPC_DEFAULTS, **s.dpc},
        keywords=resolve_keywords(s.keywords),
        vpn_bypass=resolve_vpn_bypass(s.vpn_bypass),
        network_only_unknown=tuple(unknown),
        split_apps=tuple(split),
        accessibility_only=tuple(sorted(p for p, full in ACCESSIBILITY_ONLY_APPS.items()
                                        if p in device_apps and full not in s.options)),
    )


def policy_checksum(level: int, comp: Compiled) -> str:
    """טביעה של מה שהטלפון צריך לאכוף (ושל הרמה) — לזיהוי שינוי ולאימות בטלפון."""
    material = json.dumps({"level": level, "device": comp.device_json()}, sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def summary(s: DeviceSettings) -> list[dict]:
    """רשימת החסימות הפעילות — לתצוגה (בממשק הניהול ובאפליקציה)."""
    out = [{"id": o, "title": CATALOG[o].title, "category": CATALOG[o].category} for o in s.options]
    if s.custom_hosts:
        out.append({"id": "custom_hosts", "title": f"אתרים נוספים ({len(s.custom_hosts)})", "category": "custom"})
    if s.app_rules:
        out.append({"id": "custom_apps", "title": f"אפליקציות נוספות ({len(s.app_rules)})", "category": "custom"})
    kw = resolve_keywords(s.keywords)
    if kw["enabled"] and kw["terms"]:
        out.append({"id": "keyword_block", "title": f"חסימת מילות חיפוש ({len(kw['terms'])})", "category": "custom"})
    return out
