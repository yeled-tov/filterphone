package com.filterphone.app

/**
 * דיווח מכרום (הכפתור הצף ⋮ / לחיצה ארוכה על קישור או תמונה): זיהוי תפריט ההקשר של כרום (לחיצה ארוכה)
 * וכתובת הקישור / התמונה שבראשו, וכתובת הדף מסרגל הכתובות. טהור — נבדק ביחידה.
 */
object ChromeReport {
    const val CHROME = WaBrowser.CHROME
    const val URL_BAR_ID = "com.android.chrome:id/url_bar"

    enum class Kind { SITE, LINK, IMAGE }

    /** הפריטים בתפריט של לחיצה ארוכה על קישור / תמונה (עברית / אנגלית). */
    private val LINK_ITEMS = listOf("העתקת כתובת הקישור", "העתקת טקסט הקישור", "פתיחה בכרטיסייה חדשה",
        "הורדת הקישור", "שיתוף הקישור", "copy link address", "copy link text", "open in new tab", "download link",
        "share link")
    private val IMAGE_ITEMS = listOf("הורדת התמונה", "פתיחת התמונה בכרטיסייה חדשה", "שיתוף התמונה", "העתקת התמונה",
        "חיפוש תמונה", "download image", "open image in new tab", "share image", "copy image", "search image")
    private val URL = Regex("^(https?://)?([a-z0-9-]+\\.)+[a-z]{2,}(:\\d+)?(/\\S*)?$", RegexOption.IGNORE_CASE)

    /** תפריט לחיצה ארוכה של כרום פתוח? IMAGE / LINK, או null (לא תפריט). */
    fun contextMenu(texts: List<String>): Kind? {
        val n = texts.map(::norm)
        if (n.any { t -> IMAGE_ITEMS.any { t.startsWith(it) } }) return Kind.IMAGE
        if (n.count { t -> LINK_ITEMS.any { t.startsWith(it) } } >= 2) return Kind.LINK
        return null
    }

    /** הכתובת שבראש התפריט (הקישור / התמונה) — הטקסט הראשון שנראה כמו כתובת. */
    fun menuUrl(texts: List<String>): String? {
        val urls = texts.map { it.trim() }.filter { it.length in 4..2000 && URL.matches(it) }
        // קודם כתובת מלאה (עם נתיב / https) — "photo.jpg" (הכותרת) נראה כמו כתובת אבל אינו
        return (urls.firstOrNull { it.contains("://") || it.contains('/') } ?: urls.firstOrNull())?.let(::normalize)
    }

    /** כתובת מלאה (כרום מציג בלי https://). */
    fun normalize(raw: String): String? {
        val s = raw.trim().replace(Regex("[\\u200e\\u200f\\u202a-\\u202e]"), "")
        if (s.isEmpty() || s.contains(' ')) return null
        return if (s.startsWith("http://", true) || s.startsWith("https://", true)) s else "https://$s"
    }

    /** כתובת הדף מסרגל הכתובות של כרום (מה שמוצג שם — לפעמים בלי https://). */
    fun pageUrl(barText: String?): String? {
        val t = barText?.trim().orEmpty()
        val host = t.substringAfter("://").substringBefore('/').substringBefore('?')
        return if (t.isNotEmpty() && !t.contains(' ') && URL.matches(host)) normalize(t) else null
    }

    private fun norm(s: String): String = s.replace(Regex("[\\u200e\\u200f\\u202a-\\u202e]"), "")
        .replace(Regex("\\s+"), " ").trim().lowercase()
}
