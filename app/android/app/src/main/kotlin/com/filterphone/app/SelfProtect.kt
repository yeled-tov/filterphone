package com.filterphone.app

/**
 * זיהוי מסכי ההגדרות שבהם אפשר לפגוע בהגנה של FilterPhone עצמה — כדי להקפיץ מהם החוצה:
 *   - פרטי האפליקציה של FilterPhone: "עצירה כפויה" / "אילוץ עצירה", ניקוי אחסון/נתונים, הסרה.
 *     (באנדרואיד 16, "אילוץ עצירה" מכבה את הנגישות ומפיל את ה-VPN — החור שצריך לסגור.)
 *   - ביטול מנהל המכשיר של FilterPhone (כשאין MDM, רק "מנהל מכשיר רגיל"): "השבתה" / "ביטול הפעלה".
 *
 * השיטה: מזהים שהמסך הוא של FilterPhone (מופיע שם האפליקציה) *וגם* שיש בו פעולה הרסנית — כך שמסך
 * רשימת האפליקציות (שבו רק מופיע השם) לא מקפיץ, רק הדף הספציפי של FilterPhone. טהור — נבדק ביחידה.
 */
object SelfProtect {
    // ביטויים הרסניים, עברית ואנגלית, אותיות קטנות. "עצירה" מכסה "עצירה כפויה" ו"אילוץ עצירה".
    private val DESTRUCTIVE = listOf(
        "force stop", "force close", "עצירה", "אילוץ עצירה",   // עצירה כפויה
        "uninstall", "הסרת ההתקנה", "הסרת התקנה", "הסר התקנה",   // הסרה
        "clear storage", "clear data", "clear cache",
        "ניקוי האחסון", "ניקוי אחסון", "מחיקת נתונים", "ניקוי נתונים", "ניקוי המטמון",   // ניקוי
        "deactivate", "השבתה", "השבת את", "ביטול הפעלה", "disable this device admin",   // ביטול מנהל מכשיר
    )

    private fun norm(s: String) = s.lowercase().trim()

    /**
     * המסך הוא של FilterPhone ויש בו פעולה שתפגע בהגנה. [appLabel] = שם האפליקציה (R.string.app_name).
     * [texts] = כל הטקסטים הגלויים במסך (text + contentDescription).
     */
    fun isCriticalScreen(texts: List<String>, appLabel: String): Boolean {
        val label = norm(appLabel)
        if (label.isEmpty()) return false
        val all = texts.map(::norm)
        val mentionsUs = all.any { it.contains(label) }
        if (!mentionsUs) return false
        return all.any { t -> DESTRUCTIVE.any { t.contains(it) } }
    }
}
