package com.filterphone.app

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject

/**
 * מה שהסייר המוגן זוכר — רק בטלפון, לא נשלח לשום מקום: האתרים המוצמדים לדף הבית, ההיסטוריה, והכרטיסיות
 * הפתוחות (כדי שאחרי סגירה / אתחול הן יחזרו). הלוגיקה בפונקציות טהורות (נבדקות ביחידה); השמירה ב-JSON.
 */
class BrowserData(context: Context) {
    data class Page(val title: String, val url: String, val at: Long = 0L)

    private val prefs: SharedPreferences = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    // ------------------------------------------------------------ מוצמדים

    fun pins(): List<Page> = prefs.getString(K_PINS, null)?.let(::decode) ?: DEFAULT_PINS

    fun isPinned(url: String): Boolean = pins().any { same(it.url, url) }

    fun pin(page: Page) = save(K_PINS, withPin(pins(), page))

    fun unpin(url: String) = save(K_PINS, withoutPin(pins(), url))

    // ------------------------------------------------------------ היסטוריה

    fun history(): List<Page> = prefs.getString(K_HISTORY, null)?.let(::decode) ?: emptyList()

    fun visit(page: Page) = save(K_HISTORY, withVisit(history(), page))

    fun clearHistory() = prefs.edit().remove(K_HISTORY).apply()

    // ------------------------------------------------------------ כרטיסיות פתוחות

    fun saveTabs(tabs: List<Page>, current: Int) {
        prefs.edit().putString(K_TABS, encode(tabs.take(MAX_TABS))).putInt(K_CURRENT, current).apply()
    }

    fun tabs(): Pair<List<Page>, Int> {
        val list = prefs.getString(K_TABS, null)?.let(::decode) ?: emptyList()
        return list to prefs.getInt(K_CURRENT, 0).coerceIn(0, (list.size - 1).coerceAtLeast(0))
    }

    private fun save(key: String, pages: List<Page>) = prefs.edit().putString(key, encode(pages)).apply()

    companion object {
        private const val PREFS = "fp_browser"
        private const val K_PINS = "pins"
        private const val K_HISTORY = "history"
        private const val K_TABS = "tabs"
        private const val K_CURRENT = "current_tab"
        const val MAX_PINS = 12
        const val MAX_HISTORY = 300
        const val MAX_TABS = 10

        /** בהתחלה — האתרים הכי שימושיים; הלקוח מצמיד את הבנק / הקופה שלו. */
        val DEFAULT_PINS: List<Page> = listOf("שירותי ממשלה", "האזור האישי", "ביטוח לאומי", "דואר ישראל").mapNotNull { t ->
            SafeSites.SITES.firstOrNull { it.title == t }?.let { Page(it.title, it.url) }
        }

        /** אותו דף (בלי הבדל של / בסוף, http/https או www). */
        fun same(a: String, b: String): Boolean = key(a) == key(b)

        private fun key(url: String) = url.trim().lowercase().removePrefix("https://").removePrefix("http://")
            .removePrefix("www.").trimEnd('/')

        /** הצמדה: בסוף הרשימה, בלי כפילויות, עד MAX_PINS. */
        fun withPin(pins: List<Page>, page: Page): List<Page> =
            if (pins.any { same(it.url, page.url) }) pins else (pins + page.copy(at = 0L)).takeLast(MAX_PINS)

        fun withoutPin(pins: List<Page>, url: String): List<Page> = pins.filterNot { same(it.url, url) }

        /** ביקור: למעלה ברשימה; אותו דף שוב — רק מתעדכן (לא כפילויות), עד MAX_HISTORY. */
        fun withVisit(history: List<Page>, page: Page): List<Page> {
            if (!page.url.startsWith("http")) return history
            return (listOf(page) + history.filterNot { same(it.url, page.url) }).take(MAX_HISTORY)
        }

        fun encode(pages: List<Page>): String = JSONArray().apply {
            pages.forEach { put(JSONObject().put("t", it.title).put("u", it.url).put("a", it.at)) }
        }.toString()

        fun decode(json: String): List<Page> = runCatching {
            val a = JSONArray(json)
            (0 until a.length()).mapNotNull { i ->
                val o = a.optJSONObject(i) ?: return@mapNotNull null
                val url = o.optString("u").takeIf { it.isNotEmpty() } ?: return@mapNotNull null
                Page(o.optString("t"), url, o.optLong("a"))
            }
        }.getOrDefault(emptyList())
    }
}
