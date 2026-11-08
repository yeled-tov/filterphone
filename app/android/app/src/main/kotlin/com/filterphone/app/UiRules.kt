package com.filterphone.app

import org.json.JSONArray
import org.json.JSONObject

/**
 * מנוע כללים מהשרת ("ui_rules"): השרת שולח רשימת כללי-ממשק, והאפליקציה אוכפת אותם בלי עדכון חדש.
 * כל כלל אומר למי הוא שייך (packages), מה הוא מזהה (activities / view_ids / descriptions / texts),
 * ומה לעשות (action). כך אפשר לחסום מסך או כפתור חדש (Meta AI, דפדפן פנימי, מסך חדש בוואטסאפ...)
 * מהשרת ברגע — בלי לבנות ולהפיץ אפליקציה.
 *
 * טהור — נבדק ביחידה. אין כאן קריאת הודעות: כללים מבוססים על שמות מסכים, מזהי רכיבים, תיאורים
 * וכיתובים קצרים (טקסט ארוך לא נבדק — ראו MAX_TEXT_SCAN ב-GuardService).
 */
object UiRules {
    const val MAX_RULES = 80

    enum class Action {
        BACK,        // חזרה (ברירת מחדל) — למסך קודם
        HOME,        // יציאה למסך הבית (סגירת האפליקציה/המסך)
        COVER,       // כיסוי לבן על הרכיב (הכפתור נשאר אבל מוסתר)
        SCROLL_UP;   // גלילה של הרשימה חזרה למעלה

        companion object {
            fun of(s: String?): Action = when (s?.trim()?.lowercase()) {
                "home", "close", "exit" -> HOME
                "cover", "mask", "hide" -> COVER
                "scroll_up", "scroll", "top" -> SCROLL_UP
                else -> BACK
            }
        }
    }

    data class Rule(
        val id: String,
        val packages: Set<String>,       // ריק = כל אפליקציה
        val activities: List<String>,    // תת-מחרוזות בשם המסך (מנורמל, אותיות קטנות)
        val viewIds: List<String>,       // תת-מחרוזות ב-viewIdResourceName (אותיות קטנות)
        val descriptions: List<String>,  // תת-מחרוזות ב-contentDescription (מנורמל)
        val texts: List<String>,         // תת-מחרוזות בטקסט קצר (מנורמל)
        val action: Action,
        val message: String,
    ) {
        /** כלל מסך: מזוהה לפי שם המסך (activity). */
        val isWindow get() = activities.isNotEmpty()

        /** כלל רכיב: מזוהה לפי רכיב במסך (מזהה/תיאור/טקסט). */
        val isNode get() = viewIds.isNotEmpty() || descriptions.isNotEmpty() || texts.isNotEmpty()

        /** כלל רכיב שצריך לקרוא טקסט (לא רק מזהה/תיאור) — יקר יותר, מופעל רק אם יש texts. */
        val needsText get() = texts.isNotEmpty()
    }

    /** פירוש רשימת הכללים מה-JSON של המדיניות. רשומה פגומה — מדלגים עליה. */
    fun parse(raw: Any?): List<Rule> {
        val a = raw as? JSONArray ?: return emptyList()
        val out = ArrayList<Rule>()
        for (i in 0 until minOf(a.length(), MAX_RULES)) {
            one(a.optJSONObject(i) ?: continue)?.let { out += it }
        }
        return out
    }

    private fun one(o: JSONObject): Rule? {
        val id = o.optString("id").trim()
        if (id.isEmpty()) return null
        val activities = lowerList(o, "activities")
        val viewIds = lowerList(o, "view_ids")
        val descriptions = normList(o, "descriptions")
        val texts = normList(o, "texts")
        if (activities.isEmpty() && viewIds.isEmpty() && descriptions.isEmpty() && texts.isEmpty()) return null
        return Rule(id, strList(o, "packages").toSet(), activities, viewIds, descriptions, texts,
            Action.of(o.optString("action")), o.optString("message").take(120))
    }

    private fun strList(o: JSONObject, key: String): List<String> =
        o.optJSONArray(key)?.let { a ->
            (0 until a.length()).map { a.optString(it).trim() }.filter { it.length in 1..120 }
        } ?: emptyList()

    private fun lowerList(o: JSONObject, key: String): List<String> = strList(o, key).map { it.lowercase() }
    private fun normList(o: JSONObject, key: String): List<String> =
        strList(o, key).map { norm(it) }.filter { it.isNotEmpty() }

    /** אותיות קטנות, בלי סימני כיוון/גרשיים, רווחים מכווצים. */
    fun norm(s: String): String = s.replace(Regex("[\\u200e\\u200f\\u202a-\\u202e'׳\"״`]"), "")
        .replace(Regex("\\s+"), " ").trim().lowercase()

    /** הכלל שייך לאפליקציה הזו? (packages ריק = כל אפליקציה.) טהור. */
    fun appliesTo(rule: Rule, pkg: String?): Boolean = rule.packages.isEmpty() || (pkg != null && pkg in rule.packages)

    /** מסך חסום: שם המסך (cls) מכיל אחת מתבניות ה-activities. טהור. */
    fun matchesWindow(rule: Rule, cls: String?): Boolean {
        if (!rule.isWindow || cls.isNullOrEmpty()) return false
        val c = cls.lowercase()
        return rule.activities.any { c.contains(it) }
    }

    /**
     * רכיב מזוהה: מזהה / תיאור / טקסט מכיל אחת מהתבניות. טקסט ארוך (הודעה) לא נבדק — [text] מועבר רק
     * כשהוא קצר (ראו MAX_TEXT_SCAN). טהור.
     */
    fun matchesNode(rule: Rule, viewId: String?, description: String?, text: String?): Boolean {
        if (rule.viewIds.isNotEmpty() && !viewId.isNullOrEmpty()) {
            val v = viewId.lowercase()
            if (rule.viewIds.any { v.contains(it) }) return true
        }
        if (rule.descriptions.isNotEmpty() && !description.isNullOrEmpty()) {
            val d = norm(description)
            if (rule.descriptions.any { d.contains(it) }) return true
        }
        if (rule.texts.isNotEmpty() && !text.isNullOrEmpty()) {
            val t = norm(text)
            if (rule.texts.any { t.contains(it) }) return true
        }
        return false
    }
}
