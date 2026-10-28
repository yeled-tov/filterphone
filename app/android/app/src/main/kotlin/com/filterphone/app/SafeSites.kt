package com.filterphone.app

import java.net.URLEncoder

/**
 * הסייר המוגן (BrowserActivity): האתרים החיוניים שמופיעים בטאב "סייר מוגן", ומה מותר לפתוח בו.
 *   full — גלישה רגילה (עוברת את כל הסינון, כמו Chrome) + חיפוש.
 *   essential — רק האתרים שכאן (והתת-דומיינים שלהם): ממשלה, בנקים, אשראי, קופות חולים. בלי חיפוש ברשת.
 *   off — הסייר לא נפתח (טלפון בלי גלישה: כל הדפדפנים חסומים / מסלול בלי דפדפן).
 * המצב נקבע בשרת (safe_browser במדיניות). כאן רק הלוגיקה — טהור, נבדק ביחידה.
 */
object SafeSites {
    const val FULL = "full"
    const val ESSENTIAL = "essential"
    const val OFF = "off"

    data class Site(val group: String, val title: String, val url: String, val domains: List<String>)

    private const val GOV = "ממשלה ושירותים"
    private const val BANKS = "בנקים"
    private const val CARDS = "כרטיסי אשראי"
    private const val HEALTH = "קופות חולים ובריאות"

    val SITES = listOf(
        Site(GOV, "שירותי ממשלה", "https://www.gov.il/he", listOf("gov.il")),
        Site(GOV, "האזור האישי", "https://my.gov.il", listOf("gov.il")),
        Site(GOV, "ביטוח לאומי", "https://www.btl.gov.il", listOf("gov.il")),
        Site(GOV, "רשות המסים", "https://www.gov.il/he/departments/israel_tax_authority", listOf("gov.il")),
        Site(GOV, "רשות האוכלוסין", "https://www.gov.il/he/departments/population_and_immigration_authority", listOf("gov.il")),
        Site(GOV, "דואר ישראל", "https://www.israelpost.co.il", listOf("israelpost.co.il")),
        Site(GOV, "חברת החשמל", "https://www.iec.co.il", listOf("iec.co.il")),
        Site(GOV, "רכבת ישראל", "https://www.rail.co.il", listOf("rail.co.il")),
        Site(BANKS, "בנק לאומי", "https://www.leumi.co.il", listOf("leumi.co.il", "bankleumi.co.il")),
        Site(BANKS, "בנק הפועלים", "https://www.bankhapoalim.co.il", listOf("bankhapoalim.co.il")),
        Site(BANKS, "בנק דיסקונט", "https://www.discountbank.co.il", listOf("discountbank.co.il", "telebank.co.il")),
        Site(BANKS, "מזרחי טפחות", "https://www.mizrahi-tefahot.co.il", listOf("mizrahi-tefahot.co.il", "tefahot.co.il")),
        Site(BANKS, "הבינלאומי", "https://www.fibi.co.il", listOf("fibi.co.il", "fibi-online.co.il")),
        Site(BANKS, "בנק מרכנתיל", "https://www.mercantile.co.il", listOf("mercantile.co.il")),
        Site(BANKS, "בנק ירושלים", "https://www.bankjerusalem.co.il", listOf("bankjerusalem.co.il")),
        Site(BANKS, "בנק יהב", "https://www.bank-yahav.co.il", listOf("bank-yahav.co.il")),
        Site(BANKS, "בנק מסד", "https://www.bankmassad.co.il", listOf("bankmassad.co.il")),
        Site(CARDS, "ישראכרט", "https://www.isracard.co.il", listOf("isracard.co.il")),
        Site(CARDS, "כאל", "https://www.cal-online.co.il", listOf("cal-online.co.il")),
        Site(CARDS, "מקס", "https://www.max.co.il", listOf("max.co.il")),
        Site(CARDS, "אמריקן אקספרס", "https://www.americanexpress.co.il", listOf("americanexpress.co.il")),
        Site(HEALTH, "כללית", "https://www.clalit.co.il", listOf("clalit.co.il")),
        Site(HEALTH, "מכבי", "https://www.maccabi4u.co.il", listOf("maccabi4u.co.il")),
        Site(HEALTH, "מאוחדת", "https://www.meuhedet.co.il", listOf("meuhedet.co.il")),
        Site(HEALTH, "לאומית", "https://www.leumit.co.il", listOf("leumit.co.il")),
        Site(HEALTH, "מגן דוד אדום", "https://www.mdais.org", listOf("mdais.org")),
    )

    private val DOMAINS = SITES.flatMap { it.domains }.toSet()
    private val AUTHORITY = Regex("^[a-zA-Z][a-zA-Z0-9+.-]*://(?:[^/?#@]*@)?([^/?#:@]+)")
    private val HOSTLIKE = Regex("^[\\p{L}\\d][\\p{L}\\d.-]*\\.[\\p{L}]{2,}(:\\d+)?([/?#].*)?$")

    /**
     * המצב בטלפון: מה שהשרת קבע (essential / off גוברים). השרת לא שלח (גרסה ישנה) ו-Chrome חסום — כבוי,
     * כדי שהסייר לא יהפוך לדרך לגלוש בטלפון בלי גלישה.
     */
    fun mode(serverMode: String, chromeBlocked: Boolean): String = when (serverMode) {
        ESSENTIAL -> ESSENTIAL
        OFF -> OFF
        else -> if (chromeBlocked) OFF else FULL
    }

    /** מותר לפתוח את [host] במצב [mode] (essential — רק האתרים שברשימה ותת-הדומיינים שלהם). */
    fun allows(mode: String, host: String?): Boolean {
        if (mode == FULL) return true
        if (mode != ESSENTIAL) return false
        val h = host?.lowercase()?.trimEnd('.') ?: return false
        return DOMAINS.any { h == it || h.endsWith(".$it") }
    }

    /**
     * מה שהוקלד בשורת הכתובת → כתובת לפתיחה: קישור מלא, "gov.il" → https://, ובמצב גלישה מלאה — חיפוש
     * (עם SafeSearch). null — אין מה לפתוח (ריק, או חיפוש במצב אתרים חיוניים).
     */
    fun toUrl(input: String, mode: String): String? {
        val t = input.trim()
        if (t.isEmpty()) return null
        val lower = t.lowercase()
        if (lower.startsWith("https://") || lower.startsWith("http://")) {
            return t.takeIf { hostOf(it)?.isNotEmpty() == true }
        }
        if (!t.contains(' ') && HOSTLIKE.matches(t)) return "https://$t"
        if (mode != FULL) return null
        return "https://www.google.com/search?safe=active&q=" + URLEncoder.encode(t, "UTF-8")
    }

    /** השרת בכתובת (בלי פורט), או null לכתובת פגומה. */
    fun hostOf(url: String): String? =
        AUTHORITY.find(url.trim())?.groupValues?.get(1)?.lowercase()?.takeIf { it.isNotEmpty() }

    /** קישורים שאינם דף אינטרנט: חיוג / מייל / SMS — נפתחים באפליקציה המתאימה. כל השאר (intent: וכו') — לא. */
    fun isExternalScheme(scheme: String?): Boolean = scheme?.lowercase() in setOf("tel", "mailto", "sms", "smsto")
}
