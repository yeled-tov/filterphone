package com.filterphone.app

/**
 * הדפדפן הפנימי של וואטסאפ (In-App Browser — w.meta.me): WebView בתוך וואטסאפ שלא סומך על תעודת
 * הסינון, ולכן אתרים לא נטענים בו ("האתר לא זמין"), וגם אילו נטענו — בלי סינון תמונות. זה דפדפן לכל
 * דבר, ולכן הוא חלק מ"דפדפנים חוץ מ-Chrome": המגן פותח את הקישור ב-Chrome (שעובר סינון מלא) דרך
 * התפריט של וואטסאפ (⋮ ← פתיחה בדפדפן, או ← העתקת הקישור ופתיחה מ-FilterPhone), ואם לא הצליח — סוגר
 * עם הסבר. כאן רק הזיהוי (טהור, נבדק ביחידה); הלחיצות ב-GuardService.
 */
object WaBrowser {
    /** המסכים של הדפדפן הפנימי (מה-AndroidManifest של וואטסאפ). */
    val SCREENS = listOf("IABWebCoreActivity", "WatchAndBrowseActivity", "IABWebCoreBottomSheet")

    const val CHROME = "com.android.chrome"

    fun isBrowserScreen(cls: String?): Boolean =
        cls != null && SCREENS.any { cls.contains(it, ignoreCase = true) }

    private val MORE = Regex(
        "(אפשרויות נוספות|עוד אפשרויות|^אפשרויות$|^תפריט$|^עוד$|more options|^options$|^menu$|^more$)",
        RegexOption.IGNORE_CASE,
    )
    private val EXTERNAL = Regex(
        "(פתיחה|לפתוח|פתח|פתחו|פתחי|הצגה|להציג)[^\\n]{0,24}(בדפדפן|באמצעות הדפדפן|ב-?\\s?chrome|בכרום)" +
            "|דפדפן חיצוני|דפדפן ברירת המחדל" +
            "|(open|view) (in|with) ([^\\n]{0,20})?(browser|chrome)|external browser",
        RegexOption.IGNORE_CASE,
    )
    private val COPY = Regex(
        "(העתקה|העתקת|להעתיק|העתק)[^\\n]{0,10}(קישור|הקישור|כתובת|הכתובת|לינק)|copy (link|url|address)",
        RegexOption.IGNORE_CASE,
    )
    private val URL = Regex("https?://[^\\s<>\"']+", RegexOption.IGNORE_CASE)

    /** כפתור ⋮ בסרגל העליון של הדפדפן הפנימי. */
    fun isMoreMenu(text: String?, desc: String?): Boolean =
        listOfNotNull(text, desc).any { MORE.containsMatchIn(it.trim()) }

    /** "פתיחה בדפדפן" / "Open in browser" (בסרגל או בתפריט) — לא "הגדרות" ולא "העתקת קישור". */
    fun isOpenExternal(text: String?, desc: String?): Boolean =
        listOfNotNull(text, desc).any { EXTERNAL.containsMatchIn(it) }

    /** "העתקת הקישור" בתפריט — דרך הגיבוי: FilterPhone פותחת את הקישור שהועתק ב-Chrome. */
    fun isCopyLink(text: String?, desc: String?): Boolean =
        listOfNotNull(text, desc).any { COPY.containsMatchIn(it) && !EXTERNAL.containsMatchIn(it) }

    /**
     * שדה ההקלדה של שיחה (com.whatsapp:id/entry). כל עוד הוא בחלון הפעיל — הדפדפן הפנימי עוד לא עלה,
     * וה-⋮ שבחלון הוא של השיחה (תפריט אחר לגמרי). לא לוחצים בו.
     */
    fun isComposer(viewId: String?): Boolean =
        viewId != null && viewId.substringAfter(":id/", "") == "entry"

    /** הקישור מתוך מה שהועתק (טקסט חופשי): רק http/https, בלי סימני פיסוק בסוף. null — אין קישור. */
    fun linkFrom(clip: CharSequence?): String? {
        val m = URL.find(clip ?: return null) ?: return null
        return m.value.trimEnd('.', ',', ')', ']', '}', '!', '?', ';', ':', '״', '"').takeIf { it.length > 10 }
    }

    /**
     * כתובת המעטפת של וואטסאפ (w.meta.me/s/…): עובדת רק בתוך וואטסאפ — בכרום היא נכשלת ("החיבור נסגר").
     * לכרום מעבירים רק את הקישור האמיתי (מההודעה / אחרי ההפניה).
     */
    fun isShim(text: String?): Boolean =
        text != null && SHIM.containsMatchIn(text.replace(Regex("[\\u200e\\u200f\\u202a-\\u202e]"), ""))

    private val SHIM = Regex("(^|//|\\s)w\\.meta\\.me(/|$|\\s)", RegexOption.IGNORE_CASE)
    /** "example.com/path" או "www.example.com" — בלי https (וואטסאפ הופך גם אותם לקישור). */
    private val BARE = Regex("(?<![@\\w./-])((?:www\\.(?:[a-z0-9-]+\\.)+[a-z]{2,}(?:/[^\\s<>\"']*)?)|" +
        "(?:(?:[a-z0-9-]+\\.)+[a-z]{2,}/[^\\s<>\"']+))", RegexOption.IGNORE_CASE)

    /**
     * הקישורים בטקסטים של שיחה בוואטסאפ, לפי הסדר, בלי כפילויות: http/https, וגם "example.com/path" בלי
     * https. בלי קישורים לוואטסאפ עצמו (wa.me, w.meta.me — נפתחים בוואטסאפ / לא עובדים בכרום). טהור.
     */
    fun linksIn(texts: List<String>): List<String> {
        val out = LinkedHashSet<String>()
        for (t in texts) {
            URL.findAll(t).forEach { m -> clean(m.value)?.let(out::add) }
            // בלי https — רק במה שנשאר אחרי שהוצאנו את הקישורים המלאים (לא חלק מנתיב של קישור אחר)
            BARE.findAll(URL.replace(t, " ")).forEach { m -> clean("https://${m.value}")?.let(out::add) }
        }
        return out.filter { link ->
            val host = link.substringAfter("://").substringBefore('/').lowercase()
            host != "wa.me" && !host.endsWith(".wa.me") && host != "w.meta.me" && !host.endsWith("whatsapp.com")
        }
    }

    private fun clean(s: String): String? =
        s.trimEnd('.', ',', ')', ']', '}', '!', '?', ';', ':', '״', '"', '\'').takeIf { it.length > 10 }

    /** תוויות קצרות (מה שנמצא במסך / בתפריט) לאבחון כשלא הצלחנו — בלי תוכן ארוך, עד 70 תווים. */
    fun summary(labels: List<String>): String =
        labels.asSequence().map { it.trim().replace('\n', ' ') }.filter { it.isNotEmpty() && it.length <= 40 }
            .distinct().take(10).joinToString("|").take(70)
}
