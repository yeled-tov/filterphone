package com.filterphone.app

import java.net.URI
import java.net.URLDecoder
import java.net.URLEncoder

/**
 * הסייר המוגן (BrowserActivity): האתרים החיוניים שמופיעים בטאב "סייר מוגן", ומה מותר לפתוח בו.
 *   full — גלישה רגילה (עוברת את כל הסינון, כמו Chrome) + חיפוש.
 *   essential — רק האתרים שכאן (והתת-דומיינים שלהם): ממשלה, בנקים, אשראי, קופות חולים. החיפוש הוא חיפוש
 *     בתוך האתרים האלה בלבד ("חידוש תו נכה" → הדף המדויק ב-gov.il), ראו [searchUrl].
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

    // ------------------------------------------------------------ חיפוש בתוך האתרים המאושרים

    private const val GOOGLE = "https://www.google.com/search"
    private val GOOGLE_HOSTS = setOf("www.google.com", "google.com", "www.google.co.il", "google.co.il")
    /** גוגל קורא רק ~32 מילים בשאילתה — "site:x OR" לכל אתר, אז מגבילים את מספר האתרים בכל חיפוש. */
    private const val MAX_SEARCH_DOMAINS = 12

    /** מילים שמכוונות את החיפוש לקבוצת אתרים (בלי — ממשלה ושירותים + קופות חולים). */
    private val GROUP_WORDS = linkedMapOf(
        BANKS to listOf("בנק", "חשבון", "עוש", "הלוואה", "משכנתא", "משכנתה", "פיקדון", "פקדון", "חיסכון", "צק", "שיק",
            "העברה", "הוראת קבע", "סניף", "הפועלים", "דיסקונט", "מזרחי", "טפחות", "בינלאומי", "מרכנתיל", "יהב", "מסד", "bank"),
        CARDS to listOf("אשראי", "כרטיס", "ישראכרט", "כאל", "מקס", "אמריקן", "אמקס", "isracard", "max", "cal"),
        HEALTH to listOf("רופא", "תור", "מרפאה", "בדיק", "תרופ", "מרשם", "קופת", "קופה", "כללית", "מכבי", "מאוחדת", "לאומית",
            "חיסון", "מדא", "מגן דוד", "אמבולנס", "רפוא", "בית חולים", "הפניה", "הפנייה"),
    )

    private val QUOTES = Regex("[\"'`\u05F3\u05F4]")

    private fun norm(s: String): String = s.lowercase().replace(QUOTES, "").replace(Regex("\\s+"), " ").trim()

    /** לאילו קבוצות אתרים החיפוש שייך (לפי מילים בשאילתה). */
    fun searchGroups(query: String): List<String> {
        val q = norm(query)
        val hit = GROUP_WORDS.filterValues { words -> words.any { q.contains(it) } }.keys.toList()
        return hit.ifEmpty { listOf(GOV, HEALTH) }
    }

    /** הדומיינים לחיפוש — הדומיין הראשי של כל אתר בקבוצות שנבחרו. */
    fun searchDomains(query: String): List<String> {
        val groups = searchGroups(query)
        return SITES.filter { it.group in groups }.map { it.domains.first() }.distinct().take(MAX_SEARCH_DOMAINS)
    }

    /**
     * כתובת חיפוש: full — גוגל עם SafeSearch; essential — גוגל רק בתוך האתרים המאושרים (site:), כך שהתוצאות
     * הן דפים באתרים שמותר לפתוח. null — במצב כבוי.
     */
    fun searchUrl(query: String, mode: String): String? {
        val q = query.trim().takeIf { it.isNotEmpty() } ?: return null
        return when (mode) {
            FULL -> "$GOOGLE?safe=active&q=" + URLEncoder.encode(q, "UTF-8")
            ESSENTIAL -> "$GOOGLE?safe=active&q=" + URLEncoder.encode("$q ${restriction(q)}", "UTF-8")
            else -> null
        }
    }

    private fun restriction(query: String) = searchDomains(query).joinToString(" OR ", "(", ")") { "site:$it" }

    private fun params(url: String): Pair<String, Map<String, String>>? {
        val uri = runCatching { URI(url.trim()) }.getOrNull() ?: return null
        val host = uri.host?.lowercase() ?: return null
        val map = LinkedHashMap<String, String>()
        uri.rawQuery?.split('&')?.forEach { kv ->
            val i = kv.indexOf('=')
            if (i > 0) map[kv.substring(0, i)] = runCatching { URLDecoder.decode(kv.substring(i + 1), "UTF-8") }.getOrDefault("")
        }
        return "$host${uri.rawPath ?: ""}" to map
    }

    /** דף תוצאות של גוגל (רק /search — לא תמונות, מפות, חשבון וכו'). */
    fun isGoogleSearch(url: String?): Boolean {
        val (hostPath, _) = params(url ?: return false) ?: return false
        return GOOGLE_HOSTS.any { hostPath == "$it/search" }
    }

    /**
     * באתרים מאושרים בלבד: חיפוש בגוגל בלי ההגבלה לאתרים (הקלידו שאילתה חדשה בדף התוצאות) — הכתובת המתוקנת
     * עם ההגבלה; תמונות / סרטונים / חדשות (tbm, udm) — חזרה לתוצאות הרגילות. null — הכתובת בסדר כמו שהיא.
     */
    fun restrictSearch(url: String, mode: String): String? {
        if (mode != ESSENTIAL || !isGoogleSearch(url)) return null
        val (_, p) = params(url) ?: return null
        val q = p["q"]?.trim().orEmpty()
        val restricted = q.contains("site:")
        // כבר מוגבל: פרמטרים נוספים של גוגל (ei, sa וכו') — בסדר; לא בונים מחדש, כדי לא להיכנס ללולאה מול הפניות
        if (restricted && p.keys.none { it in OTHER_TABS }) return null
        if (q.isEmpty()) return null
        val start = p["start"]?.takeIf { s -> s.all { it.isDigit() } }
        val query = if (restricted) q else "$q ${restriction(q)}"
        return "$GOOGLE?safe=active&q=" + URLEncoder.encode(query, "UTF-8") + (start?.let { "&start=$it" } ?: "")
    }

    /** הטאבים של גוגל שאינם תוצאות רגילות (תמונות, סרטונים, חדשות). */
    private val OTHER_TABS = setOf("tbm", "udm")

    /** קישור תוצאה של גוגל שעובר דרך /url?q=… — הכתובת האמיתית (כדי לבדוק אותה, ולא את גוגל). */
    fun unwrapRedirect(url: String?): String? {
        val (hostPath, p) = params(url ?: return null) ?: return null
        if (GOOGLE_HOSTS.none { hostPath == "$it/url" }) return null
        val target = p["q"] ?: p["url"] ?: return null
        return target.takeIf { it.startsWith("https://") || it.startsWith("http://") }
    }

    /** הצעות מהרשימה בזמן ההקלדה: אתר שהשם או הכתובת שלו מתאימים. */
    fun suggest(query: String, limit: Int = 5): List<Site> {
        val q = norm(query)
        if (q.length < 2) return emptyList()
        return SITES.filter { norm(it.title).contains(q) || it.domains.any { d -> d.contains(q) } }
            .distinctBy { it.url }.take(limit)
    }

    /** מותר לפתוח את [url] (דף שלם) במצב [mode]: אתר מהרשימה, או חיפוש בגוגל (שמוגבל לאתרים — restrictSearch). */
    fun allowsUrl(mode: String, url: String?): Boolean {
        if (url == null) return false
        if (mode == FULL) return true
        if (mode != ESSENTIAL) return false
        return allows(mode, hostOf(url)) || isGoogleSearch(url)
    }

    /** מותר לפתוח את [host] במצב [mode] (essential — רק האתרים שברשימה ותת-הדומיינים שלהם). */
    fun allows(mode: String, host: String?): Boolean {
        if (mode == FULL) return true
        if (mode != ESSENTIAL) return false
        val h = host?.lowercase()?.trimEnd('.') ?: return false
        return DOMAINS.any { h == it || h.endsWith(".$it") }
    }

    /**
     * מה שהוקלד בשורת הכתובת → כתובת לפתיחה: קישור מלא, "gov.il" → https://, ואחרת — חיפוש ([searchUrl]:
     * בגלישה מלאה עם SafeSearch, באתרים מאושרים — רק בתוכם). null — אין מה לפתוח.
     */
    fun toUrl(input: String, mode: String): String? {
        val t = input.trim()
        if (t.isEmpty()) return null
        val lower = t.lowercase()
        if (lower.startsWith("https://") || lower.startsWith("http://")) {
            return t.takeIf { hostOf(it)?.isNotEmpty() == true }
        }
        if (!t.contains(' ') && HOSTLIKE.matches(t)) return "https://$t"
        return searchUrl(t, mode)
    }

    /** השרת בכתובת (בלי פורט), או null לכתובת פגומה. */
    fun hostOf(url: String): String? =
        AUTHORITY.find(url.trim())?.groupValues?.get(1)?.lowercase()?.takeIf { it.isNotEmpty() }

    /** קישורים שאינם דף אינטרנט: חיוג / מייל / SMS — נפתחים באפליקציה המתאימה. כל השאר (intent: וכו') — לא. */
    fun isExternalScheme(scheme: String?): Boolean = scheme?.lowercase() in setOf("tel", "mailto", "sms", "smsto")
}
