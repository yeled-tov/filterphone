package com.filterphone.app

/**
 * הדפדפן הפנימי של וואטסאפ (In-App Browser — w.meta.me): WebView בתוך וואטסאפ שלא סומך על תעודת
 * הסינון, ולכן אתרים לא נטענים בו ("האתר לא זמין"), וגם אילו נטענו — בלי סינון תמונות. זה דפדפן לכל
 * דבר, ולכן הוא חלק מ"דפדפנים חוץ מ-Chrome": המגן פותח את הקישור ב-Chrome (שעובר סינון מלא) דרך
 * התפריט של וואטסאפ (⋮ ← פתיחה בדפדפן), ואם לא הצליח — סוגר עם הסבר. כאן רק הזיהוי (טהור, נבדק ביחידה).
 */
object WaBrowser {
    /** המסכים של הדפדפן הפנימי (מה-AndroidManifest של וואטסאפ). */
    val SCREENS = listOf("IABWebCoreActivity", "WatchAndBrowseActivity", "IABWebCoreBottomSheet")

    fun isBrowserScreen(cls: String?): Boolean =
        cls != null && SCREENS.any { cls.contains(it, ignoreCase = true) }

    private val MORE = Regex("(אפשרויות נוספות|עוד אפשרויות|more options|^תפריט$|^menu$)", RegexOption.IGNORE_CASE)
    private val EXTERNAL = Regex(
        "(פתיחה|לפתוח|פתח|פתחו)[^\\n]{0,20}(בדפדפן|ב-?chrome|בכרום)|open (in|with) ([a-z ]{0,12})?(browser|chrome)",
        RegexOption.IGNORE_CASE,
    )

    /** כפתור ⋮ בסרגל העליון של הדפדפן הפנימי. */
    fun isMoreMenu(text: String?, desc: String?): Boolean =
        listOfNotNull(text, desc).any { MORE.containsMatchIn(it.trim()) }

    /** "פתיחה בדפדפן" / "Open in browser" בתפריט — לא "הגדרות" ולא "העתקת קישור". */
    fun isOpenExternal(text: String?, desc: String?): Boolean =
        listOfNotNull(text, desc).any { EXTERNAL.containsMatchIn(it) }
}
