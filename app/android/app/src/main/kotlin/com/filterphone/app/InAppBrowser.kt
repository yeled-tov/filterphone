package com.filterphone.app

/**
 * דפדפן פנימי (In-App Browser) באפליקציות: WebView בתוך אפליקציה (Waze, Moovit וכו') שדרכו אפשר להגיע
 * לאתרים — בלי סינון התמונות של הפרוקסי (ה-WebView לא סומך על תעודת הסינון, ולכן הפרוקסי "לומד" לעקוף
 * אותו). לכן חוסמים אותו: סוגרים את המסך, ואם יש כתובת בשורת הכתובת — פותחים אותה ב-Chrome (שעובר סינון
 * מלא). רק באפליקציות שהשרת ציין (inapp_browser) — כדי לא לפגוע בתוכן web לגיטימי (מייל, התחברות).
 *
 * הזיהוי: המסך מכיל WebView וגם שורת כתובת עם כתובת http(s) חיצונית — שילוב ששייך לדפדפן, לא לדף web
 * מוטמע (תצוגת מייל, מסך התחברות) שאין בו שורת כתובת. טהור — נבדק ביחידה.
 */
object InAppBrowser {
    const val CHROME = "com.android.chrome"

    /** רכיב WebView (className). */
    fun isWebView(cls: String?): Boolean = cls != null && cls.contains("WebView", ignoreCase = true)

    private val URL = Regex("https?://[^\\s<>\"'\\u200e\\u200f]{4,}", RegexOption.IGNORE_CASE)
    private val BARE = Regex(
        "^(?:www\\.)?(?:[a-z0-9-]+\\.)+[a-z]{2,}(?::\\d+)?(?:/\\S*)?$",
        RegexOption.IGNORE_CASE,
    )

    /**
     * כתובת מתוך שורת כתובת (טקסט קצר שכולו ~URL) — לא כל טקסט שיש בו נקודה. http(s), או דומיין/נתיב מלא
     * בלי https. null אם זה לא נראה כמו שורת כתובת. טהור.
     */
    fun addressBarUrl(text: String?): String? {
        val t = (text ?: return null).replace(Regex("[\\u200e\\u200f\\u202a-\\u202e]"), "").trim()
        if (t.isEmpty() || t.length > 2048 || t.contains(' ')) return null   // שורת כתובת — בלי רווחים
        URL.find(t)?.let { return clean(it.value) }
        if (t.length <= 300 && BARE.matches(t)) return clean("https://$t")
        return null
    }

    private fun clean(s: String): String? =
        s.trimEnd('.', ',', ')', ']', '}', '!', '?', ';', ':', '״', '"', '\'').takeIf { it.length > 10 }

    /**
     * המסך הוא דפדפן פנימי? (יש WebView וגם שורת כתובת עם כתובת.) מקבל את הרכיבים שנאספו מהמסך.
     * מחזיר את הכתובת (לפתיחה ב-Chrome), או "" אם זוהה דפדפן בלי כתובת קריאה (עדיין נחסם), או null אם לא
     * דפדפן. טהור.
     */
    fun detect(nodes: Sequence<Node>): String? {
        var webView = false
        var url: String? = null
        for (n in nodes) {
            if (!webView && isWebView(n.className)) webView = true
            if (url == null) addressBarUrl(n.text)?.let { url = it }
        }
        if (!webView) return null              // בלי WebView — לא דפדפן (שלט/תווית עם קישור בלבד לא נחשב)
        return url ?: ""                       // "" = דפדפן בלי כתובת קריאה — עדיין נחסם (סגירה בלבד)
    }

    data class Node(val className: String?, val text: String?)
}
