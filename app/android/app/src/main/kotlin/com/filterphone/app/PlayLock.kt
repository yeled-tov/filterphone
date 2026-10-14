package com.filterphone.app

/**
 * "שלב א'" של החנות: חלון Google Play נעול לאפליקציה אחת.
 *
 * כש-Play חסום לכניסה (מגן FilterPhone), החנות שלנו פותחת את העמוד של אפליקציה מאושרת ב-Play
 * ומתחילה "חלון התקנה" לאפליקציה הזו בלבד. בזמן החלון המגן מתיר רק:
 *   • את עמוד האפליקציה עצמה (מזוהה לפי השם שלה ב-Play, בראש המסך),
 *   • וחלונות ביניים של ההתקנה (חשבון, הרשאות, טעינה) — כל מה שאינו "ניווט החוצה".
 * ניווט החוצה — דף הבית של Play, חיפוש, או עמוד של אפליקציה אחרת — מחזיר מיד לעמוד המבוקש.
 * אחרי שהאפליקציה הותקנה/עודכנה (או כשהזמן נגמר, או כשיוצאים מ-Play) — Play חוזר להיות חסום.
 *
 * כאן רק ההחלטה (טהור, נבדק ביחידה) ומצב החלון; הביצוע ב-GuardService.
 */
object PlayLock {
    const val PLAY = "com.android.vending"
    /** כמה זמן חלון ההתקנה פתוח לכל היותר. */
    const val WINDOW_MS = 10 * 60_000L
    /** אחרי שההתקנה/העדכון הסתיימו — עוד דקה (כדי ללחוץ "פתיחה"), ואז נסגר. */
    const val AFTER_INSTALL_MS = 60_000L
    /** טקסטים מראש המסך (לפי סדר הרכיבים) שבהם מחפשים את שם האפליקציה. */
    const val HEAD_TEXTS = 30

    /** חבילות שמותר לעבור אליהן בלי לסגור את החלון (בחירת חשבון, אישורי התקנה, המקלדת...). */
    val COMPANIONS = setOf(PLAY, "com.google.android.gms", "com.google.android.packageinstaller",
        "com.android.packageinstaller", "com.android.systemui")

    data class Session(
        val pkg: String,
        val titles: List<String>,
        val until: Long,
        /** הגרסה שהייתה מותקנת כשהחלון נפתח (null = לא מותקנת) — כדי לזהות שההתקנה הסתיימה. */
        val versionAtStart: Long?,
    )

    enum class Verdict { ALLOW, RETURN }

    @Volatile var session: Session? = null
        private set

    fun start(pkg: String, titles: List<String>, versionAtStart: Long?, now: Long) {
        val clean = titles.map(::norm).filter { it.length >= 2 }.distinct()
        session = Session(pkg, clean, now + WINDOW_MS, versionAtStart)
    }

    fun end() {
        session = null
    }

    fun active(now: Long): Session? = session?.takeIf { now < it.until }

    /** ההתקנה/העדכון הסתיימו (הגרסה המותקנת השתנתה): משאירים דקה ל"פתיחה", ואז נסגר. */
    fun onVersion(installed: Long?, now: Long) {
        val s = session ?: return
        if (installed != null && installed != s.versionAtStart) {
            session = s.copy(until = minOf(s.until, now + AFTER_INSTALL_MS))
        }
    }

    /**
     * מה לעשות בחלון של Play. [texts] — הטקסטים בסדר הרכיבים (מלמעלה למטה); [searchField] — יש
     * שדה הקלדה (מסך החיפוש של Play). עמוד האפליקציה עצמה תמיד מותר; ניווט החוצה — חזרה.
     */
    fun verdict(texts: List<String>, searchField: Boolean, titles: List<String>): Verdict {
        val all = texts.map(::norm).filter { it.isNotEmpty() }
        val head = all.take(HEAD_TEXTS)
        if (titles.any { t -> head.any { h -> h == t || (t.length >= 4 && h.startsWith(t)) } }) return Verdict.ALLOW
        if (searchField) return Verdict.RETURN
        if (all.count { it in NAV_LABELS } >= 2) return Verdict.RETURN            // דף הבית / לשוניות של Play
        if (all.any { it in INSTALL_BUTTONS }) return Verdict.RETURN              // עמוד של אפליקציה אחרת
        return Verdict.ALLOW                                                       // חלון ביניים של ההתקנה
    }

    /** הלשוניות בתחתית Play (עברית/אנגלית). שתיים יחד = לא עמוד של אפליקציה. */
    private val NAV_LABELS = setOf("משחקים", "אפליקציות", "ספרים", "חיפוש", "בשבילך", "מבצעים", "ילדים",
        "games", "apps", "books", "search", "kids", "offers", "for you")

    /** כפתורי הפעולה בעמוד אפליקציה. מופיעים בלי השם שלנו בראש המסך = עמוד של אפליקציה אחרת. */
    private val INSTALL_BUTTONS = setOf("התקנה", "התקן", "עדכון", "פתיחה", "הסרת התקנה", "קנייה",
        "install", "update", "open", "uninstall", "buy")

    private fun norm(s: String): String = s.replace(Regex("[\\u200e\\u200f\\u202a-\\u202e]"), "")
        .replace(Regex("\\s+"), " ").trim().lowercase()
}
