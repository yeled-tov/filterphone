package com.filterphone.app

/**
 * זיהוי "צפייה בתמונת פרופיל" בוואטסאפ — בכמה שכבות, כי וואטסאפ משנה את המסכים שלו בין גרסאות:
 *
 *   1. שם החלון / ה-Activity (למשל ViewProfilePhoto, QuickContact). הזול והמדויק ביותר.
 *   2. מזהה הרכיב (view id) של תמונה גדולה (למשל profile_picture) — כשהתמונה נפתחת בתוך אותו
 *      מסך (Fragment) ושם החלון לא משתנה.
 *   3. תיאור הנגישות של רכיב תמונה ("Profile photo" / "תמונת פרופיל").
 *   שכבות 2–3 דורשות גם שהתמונה תתפוס חלק גדול מהמסך (min_area) — כך אייקון קטן ברשימת
 *   הצ'אטים לא נחשב "צפייה".
 *   אם אף שכבה לא בטוחה — לא עושים כלום (עדיף לא לחסום בטעות מסך אחר בוואטסאפ).
 *
 * פרטיות: מקבלים רק שם מחלקה, מזהה רכיב, תיאור של רכיבי תמונה ושטח. לא נוגעים בטקסט של
 * הודעות / שדות הקלדה, ולא שומרים כלום. התבניות מגיעות מהשרת (מתעדכנות בלי גרסה חדשה).
 *
 * המחלקה הזו בלי תלות באנדרואיד — נבדקת בבדיקות יחידה.
 */
class WaProfileDetector(private val config: GuardPolicy.WaDetector) {

    /** רכיב אחד מעץ הנגישות — רק השדות שמותר להשתמש בהם. */
    data class Node(
        val viewId: String?,        // "com.whatsapp:id/profile_picture"
        val className: String?,     // "android.widget.ImageView"
        val description: String?,  // תיאור נגישות — נבדק רק ברכיבי תמונה
        val areaFraction: Float,    // שטח הרכיב / שטח המסך
    )

    fun watches(pkg: String?): Boolean = pkg != null && pkg in config.packages

    /** מסכים שלעולם לא נחשבים צפייה בתמונת פרופיל (מדיה בצ'אט, גלריה, מצלמה, סטטוס — לסטטוס יש חסימה משלו). */
    fun excluded(className: String?): Boolean {
        val cls = className?.lowercase() ?: return false
        return config.excludeWindows.any { cls.contains(it) }
    }

    /** שכבה 1: שם החלון. מחזיר את הכלל שהתאים, או null. */
    fun matchWindow(pkg: String?, className: String?): String? {
        if (!watches(pkg) || className.isNullOrEmpty()) return null
        val cls = className.lowercase()
        return config.activities.firstOrNull { cls.contains(it) }?.let { "activity:$it" }
    }

    /** שכבות 2–3: רכיבים במסך. מחזיר את הכלל שהתאים, או null. */
    fun matchNodes(nodes: Sequence<Node>): String? {
        for (n in nodes) {
            if (n.areaFraction < config.minArea) continue
            val image = isImage(n)
            val id = n.viewId?.substringAfter(":id/", n.viewId)?.lowercase()
            if (id != null && (image || id.contains("photo") || id.contains("picture"))) {
                config.viewIds.firstOrNull { id == it || id.endsWith("_$it") || id.startsWith("${it}_") }
                    ?.let { return "view_id:$it" }
            }
            if (image) {
                val desc = n.description?.lowercase() ?: continue
                config.descriptions.firstOrNull { desc.contains(it) }?.let { return "description" }
            }
        }
        return null
    }

    private fun isImage(n: Node): Boolean {
        val c = n.className ?: return false
        return c.endsWith("ImageView") || c.endsWith("ImageButton") || c.contains("Photo", ignoreCase = true)
    }
}
