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
 * בעמוד האפליקציה עצמה — מסך של FilterPhone מכסה את Play כולו (PlayInstallOverlay): אי אפשר לגעת, לגלול
 * או לנווט ב-Play. FilterPhone לוחץ בעצמו על "התקנה" / "עדכון" (רק בעמוד של האפליקציה שנבחרה), מציג
 * התקדמות, ובסיום חוזר לחנות של FilterPhone. המסך עולה עוד לפני ש-Play נפתח ונשאר עד הסוף — הלקוח לא רואה
 * את Play בכלל. Play תקוע / מבקש משהו שאי אפשר לעשות אוטומטית — "נסה שוב" או חזרה.
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
        /** השם להצגה במסך ההתקנה. */
        val label: String = "",
        /** כמה פעמים FilterPhone לחץ "התקנה" / "עדכון" ב-Play, ומתי בפעם האחרונה. */
        val clicks: Int = 0,
        val lastClickAt: Long = 0,
        /** מתי החלון נפתח. */
        val startedAt: Long = 0,
    )

    /** מצב עמוד האפליקציה ב-Play, לפי הכפתורים בו. */
    enum class Action { INSTALL, UPDATE, INSTALLING, INSTALLED, UNKNOWN }

    const val MAX_CLICKS = 3
    const val CLICK_RETRY_MS = 4_000L
    /** בלי התקדמות כמה זמן (לא התחילה הורדה) — "החנות לא מגיבה", עם "נסה שוב". */
    const val STUCK_MS = 45_000L

    enum class Verdict { ALLOW, RETURN }

    @Volatile var session: Session? = null
        private set

    fun start(pkg: String, titles: List<String>, versionAtStart: Long?, now: Long) {
        val clean = titles.map(::norm).filter { it.length >= 2 }.distinct()
        val label = titles.firstOrNull { it.isNotBlank() }?.trim().orEmpty()
        session = Session(pkg, clean, now + WINDOW_MS, versionAtStart, label = label, startedAt = now)
    }

    /** FilterPhone לחץ על הכפתור ב-Play. */
    fun noteClick(now: Long) {
        session = session?.let { it.copy(clicks = it.clicks + 1, lastClickAt = now) }
    }

    /** "נסה שוב": מתחילים את הספירה מחדש (עוד 3 לחיצות, עוד זמן עד "לא מגיבה"). */
    fun retry(now: Long) {
        session = session?.copy(clicks = 0, lastClickAt = 0, startedAt = now, until = maxOf(session!!.until, now + WINDOW_MS / 2))
    }

    /** תקוע: לא התחילה הורדה כבר STUCK_MS מאז הפתיחה / הלחיצה האחרונה / "נסה שוב". טהור. */
    fun stuck(s: Session, lastProgressAt: Long, now: Long): Boolean =
        now - maxOf(s.startedAt, s.lastClickAt, lastProgressAt) >= STUCK_MS

    /** ללחוץ עכשיו על "התקנה" / "עדכון"? עד MAX_CLICKS פעמים, בהפרש של CLICK_RETRY_MS. טהור. */
    fun shouldClick(s: Session, action: Action, now: Long): Boolean =
        (action == Action.INSTALL || action == Action.UPDATE) && s.clicks < MAX_CLICKS && now - s.lastClickAt >= CLICK_RETRY_MS

    /** ההתקנה / העדכון הסתיימו בחלון הזה (הגרסה המותקנת השתנתה). טהור. */
    fun installedDuring(s: Session, installed: Long?): Boolean = installed != null && installed != s.versionAtStart

    /**
     * מצב העמוד לפי הכפתור הראשי — הראשון בסדר הרכיבים (התאמה מדויקת — "מספר התקנות" או "עודכן ב-" לא
     * נחשבים). טהור. רק הכפתור הראשי: מתחתיו יש פרסומות ("ממומן", "אולי תאהבו") עם "התקנה" / "עדכון"
     * משלהן — הן לא קובעות את המצב, אחרת FilterPhone לוחץ על אפליקציה אחרת (Temu התעדכנה בכל התקנה).
     * "הסרת התקנה" ראשון = מותקנת; הכפתור שאחריו קובע: עדכון / פתיחה / ביטול.
     */
    fun action(texts: List<String>): Action {
        val all = texts.map(::norm).filter { it.isNotEmpty() }
        for ((i, t) in all.withIndex()) {
            when {
                t in UPDATE_BUTTONS -> return Action.UPDATE
                t in INSTALL_ONLY -> return Action.INSTALL
                t in CANCEL_BUTTONS || PERCENT.containsMatchIn(t) || PENDING.any { t.startsWith(it) } ->
                    return Action.INSTALLING
                t in OPEN_BUTTONS -> return Action.INSTALLED
                t in UNINSTALL_BUTTONS -> {
                    val next = all.subList(i + 1, minOf(all.size, i + 1 + AFTER_UNINSTALL))
                        .firstOrNull { it in UPDATE_BUTTONS || it in OPEN_BUTTONS || it in CANCEL_BUTTONS }
                    return when (next) {
                        in UPDATE_BUTTONS -> Action.UPDATE
                        in CANCEL_BUTTONS -> Action.INSTALLING
                        else -> Action.INSTALLED
                    }
                }
            }
        }
        return Action.UNKNOWN
    }

    /** כפתור שיכול להיות הכפתור הראשי של עמוד אפליקציה (התקנה / עדכון / ביטול / פתיחה / הסרה). */
    fun isPrimaryLabel(text: String?): Boolean = text != null && norm(text) in PRIMARY_LABELS

    /**
     * על איזה כפתור ללחוץ: [labels] — הכיתובים של הרכיבים בסדר שלהם (null = בלי כיתוב). רק הכפתור הראשי
     * של העמוד: הכפתור הראשון, או — כשהראשון הוא "הסרת התקנה" — הכפתור שאחריו (עדכון / פתיחה / ביטול).
     * אף פעם לא "התקנה" / "עדכון" של פרסומת או המלצה בהמשך העמוד. null = הכפתור הראשי אינו ב-[wanted]. טהור.
     */
    fun primaryIndex(labels: List<String?>, wanted: Set<String>): Int? {
        val idx = labels.indices.filter { i -> labels[i]?.let(::norm)?.let { it in PRIMARY_LABELS } == true }
        val first = idx.firstOrNull() ?: return null
        val f = norm(labels[first]!!)
        if (f in wanted) return first
        if (f !in UNINSTALL_BUTTONS) return null
        val second = idx.firstOrNull { norm(labels[it]!!) !in UNINSTALL_BUTTONS } ?: return null
        return second.takeIf { norm(labels[it]!!) in wanted }
    }

    /** כבר ידוע מהו הכפתור הראשי (מכאן — פרסומות והמלצות; אפשר להפסיק לסרוק). טהור. */
    fun primaryDecided(labels: List<String?>): Boolean {
        val p = labels.mapNotNull { l -> l?.let(::norm)?.takeIf { it in PRIMARY_LABELS } }
        return p.isNotEmpty() && (p[0] !in UNINSTALL_BUTTONS || p.any { it !in UNINSTALL_BUTTONS })
    }

    /** אחוז ההורדה שמוצג ב-Play ("45%"), אם יש. טהור. */
    fun progress(texts: List<String>): Int? = texts.firstNotNullOfOrNull { t ->
        PERCENT.find(norm(t))?.groupValues?.get(1)?.toIntOrNull()?.takeIf { it in 0..100 }
    }

    /** הטקסטים של הכפתור שעליו FilterPhone לוחץ (לפי המצב). */
    fun buttonTexts(action: Action): Set<String> = when (action) {
        Action.UPDATE -> UPDATE_BUTTONS
        Action.INSTALL -> INSTALL_ONLY
        Action.INSTALLING -> CANCEL_BUTTONS
        Action.INSTALLED -> OPEN_BUTTONS
        Action.UNKNOWN -> emptySet()
    }

    fun matches(text: String?, set: Set<String>): Boolean = text != null && norm(text) in set

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
        if (isTargetPage(texts, titles)) return Verdict.ALLOW
        if (searchField) return Verdict.RETURN
        if (all.count { it in NAV_LABELS } >= 2) return Verdict.RETURN            // דף הבית / לשוניות של Play
        if (all.any { it in INSTALL_BUTTONS }) return Verdict.RETURN              // עמוד של אפליקציה אחרת
        return Verdict.ALLOW                                                       // חלון ביניים של ההתקנה
    }

    /** זה העמוד של האפליקציה שנבחרה: השם שלה בראש המסך. */
    fun isTargetPage(texts: List<String>, titles: List<String>): Boolean {
        val head = texts.asSequence().map(::norm).filter { it.isNotEmpty() }.take(HEAD_TEXTS).toList()
        return titles.any { t ->
            head.any { h ->
                h == t ||
                    (t.length >= 4 && h.startsWith(t)) ||     // כותרת Play ארוכה יותר (שם מלא של האפליקציה)
                    (h.length >= 4 && t.startsWith(h))        // שם האפליקציה בטלפון ארוך יותר מהכותרת ב-Play
            }
        }
    }

    /** כפתורי עמוד אפליקציה ב-Play (עברית / אנגלית), לפי מצב. */
    private val INSTALL_ONLY = setOf("התקנה", "התקן", "install")
    private val UPDATE_BUTTONS = setOf("עדכון", "עדכן", "update")
    private val CANCEL_BUTTONS = setOf("ביטול", "בטל", "cancel")
    private val OPEN_BUTTONS = setOf("פתיחה", "פתח", "הפעלה", "open", "play", "enable")
    private val UNINSTALL_BUTTONS = setOf("הסרת התקנה", "הסר התקנה", "uninstall")
    private val PRIMARY_LABELS = INSTALL_ONLY + UPDATE_BUTTONS + CANCEL_BUTTONS + OPEN_BUTTONS + UNINSTALL_BUTTONS
    private const val AFTER_UNINSTALL = 4
    private val PENDING = listOf("ממתין", "בהמתנה", "מוריד", "מתקין", "בהורדה", "pending", "waiting", "downloading",
        "installing")
    private val PERCENT = Regex("(\\d{1,3})\\s?%")

    /** הלשוניות בתחתית Play (עברית/אנגלית). שתיים יחד = לא עמוד של אפליקציה. */
    private val NAV_LABELS = setOf("משחקים", "אפליקציות", "ספרים", "חיפוש", "בשבילך", "מבצעים", "ילדים",
        "games", "apps", "books", "search", "kids", "offers", "for you")

    /** כפתורי הפעולה בעמוד אפליקציה. מופיעים בלי השם שלנו בראש המסך = עמוד של אפליקציה אחרת. */
    private val INSTALL_BUTTONS = setOf("התקנה", "התקן", "עדכון", "פתיחה", "הסרת התקנה", "קנייה",
        "install", "update", "open", "uninstall", "buy")

    private fun norm(s: String): String = s.replace(Regex("[\\u200e\\u200f\\u202a-\\u202e]"), "")
        .replace(Regex("\\s+"), " ").trim().lowercase()
}
