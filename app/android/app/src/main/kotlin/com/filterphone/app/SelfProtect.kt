package com.filterphone.app

/**
 * זיהוי המקומות שבהם אפשר לפגוע בהגנה של FilterPhone עצמה — כדי להקפיץ מהם החוצה:
 *   - פרטי האפליקציה של FilterPhone: "עצירה כפויה" / "אילוץ עצירה", ניקוי אחסון/נתונים, הסרה, העברה
 *     לארכיון. (באנדרואיד 16, "אילוץ עצירה" מכבה את הנגישות ומפיל את ה-VPN — החור שצריך לסגור.)
 *   - ביטול מנהל המכשיר של FilterPhone (כשאין MDM, רק "מנהל מכשיר רגיל"): "השבתה" / "ביטול הפעלה".
 *   - לחיצה ארוכה על הסמל של FilterPhone במסך הבית / במגירה — התפריט שממנו מגיעים ל"מידע על האפליקציה".
 *
 * רק של FilterPhone: הדף נחשב "שלנו" כשהשם שלנו הוא הכותרת שלו (טקסט שהוא בדיוק השם, או שמתחיל בו בראש
 * המסך) *וגם* יש בו פעולה הרסנית. כך דפים של אפליקציות אחרות — גם כאלה שכתוב בהם "הותקנה מ-FilterPhone"
 * — וגם רשימות (מנהלי מכשיר, כל האפליקציות) נשארים פתוחים. טהור — נבדק ביחידה.
 */
object SelfProtect {
    // ביטויים הרסניים, עברית ואנגלית, אותיות קטנות. "עצירה"/"עצור" מכסים "עצירה כפויה", "אילוץ עצירה",
    // "עצור בכפייה"; "השבת" מכסה "השבתה", "השבתת", "השבת את".
    private val DESTRUCTIVE = listOf(
        "force stop", "force close", "עצירה", "עצור",                                       // עצירה כפויה
        "uninstall", "הסרת ההתקנה", "הסרת התקנה", "הסר התקנה", "הסרה",                      // הסרה
        "archive", "העברה לארכיון", "לארכיון",                                              // אנדרואיד 15+
        "clear storage", "clear data", "clear cache",
        "ניקוי האחסון", "ניקוי אחסון", "מחיקת נתונים", "ניקוי נתונים", "ניקוי המטמון",       // ניקוי
        "deactivate", "השבת", "ביטול הפעלה", "disable this device admin",                    // ביטול מנהל מכשיר
    )

    /** כמה טקסטים מראש המסך נבדקים לכותרת שמתחילה בשם (למשל "FilterPhone" + גרסה באותה שורה). */
    private const val HEAD = 8

    /** פריטי התפריט שנפתח בלחיצה ארוכה על סמל (פיקסל, סמסונג, שיאומי...). */
    private val LAUNCHER_MENU = listOf(
        "app info", "מידע על האפליקציה", "מידע על אפליקציה", "פרטי האפליקציה", "פרטי אפליקציה",
        "pause app", "השהיית האפליקציה", "השהיית אפליקציה", "השהה אפליקציה",
        "uninstall", "הסרת התקנה", "הסר התקנה",
    )

    private val BIDI = Regex("[\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069]")

    private fun norm(s: String) = s.replace(BIDI, "").replace(Regex("\\s+"), " ").trim().lowercase()

    /** הכותרת של המסך היא השם שלנו (ולא רק אזכור באמצע, כמו "הותקנה מ-FilterPhone"). */
    private fun titledUs(all: List<String>, label: String): Boolean =
        all.any { it == label } || all.take(HEAD).any { it.startsWith("$label ") || it.startsWith("$label,") }

    /**
     * המסך הוא של FilterPhone ויש בו פעולה שתפגע בהגנה. [appLabel] = שם האפליקציה (כפי שמוצג בהגדרות).
     * [texts] = הטקסטים הגלויים במסך (text + contentDescription), מלמעלה למטה ככל האפשר.
     */
    fun isCriticalScreen(texts: List<String>, appLabel: String): Boolean {
        val label = norm(appLabel)
        if (label.isEmpty()) return false
        val all = texts.map(::norm).filter { it.isNotEmpty() }
        if (!titledUs(all, label)) return false
        return all.any { t -> DESTRUCTIVE.any { t.contains(it) } }
    }

    /**
     * לחיצה ארוכה במסך הבית הייתה על הסמל של FilterPhone. [texts] — הטקסט / התיאור של הרכיב שנלחץ
     * (לפעמים עם תוספת: "FilterPhone, 2 התראות").
     */
    fun isOurIcon(texts: List<String>, appLabel: String): Boolean {
        val label = norm(appLabel)
        if (label.isEmpty()) return false
        return texts.map(::norm).any { it == label || it.startsWith("$label,") || it.startsWith("$label ") }
    }

    /** תפריט הלחיצה הארוכה עדיין פתוח במסך הבית (יש בו "מידע על האפליקציה" / "הסרת התקנה" וכו'). */
    fun isLauncherMenu(texts: List<String>): Boolean =
        texts.map(::norm).any { t -> LAUNCHER_MENU.any { t == it || (it.length >= 8 && t.startsWith(it)) } }
}
