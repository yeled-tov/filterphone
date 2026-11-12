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
 * מסך פרטי איש קשר / קבוצה (לוחצים על השם בראש השיחה) — מותר: בראשו תמונת פרופיל גדולה, אבל היא חלק מעמוד
 * עם מדיה, קטלוג וכו'. מזהים אותו לפי שם החלון (info_windows) או לפי המבנה — התמונה הגדולה על מסך עם רשימה
 * נגללת גדולה. שם לא יוצאים: AvatarMask מכסה את התמונה ([Verdict.infoPhoto]). רק תמונה שממש ממלאת את המסך
 * (info_min_area) נחשבת צפייה גם שם.
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
        val scrollable: Boolean = false,   // רשימה / עמוד נגלל (מסך פרטים), לא צפייה בתמונה
    )

    /** תוצאת סריקה: [rule] — צפייה בתמונה (לצאת); [infoPhoto] — תמונה גדולה בעמוד פרטים (לכסות, להישאר). */
    data class Verdict(val rule: String?, val infoPhoto: Boolean = false)

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

    /** מסך פרטי איש קשר / קבוצה / עסק (לפי שם החלון) — מותר, רק התמונה מכוסה. */
    fun isInfoWindow(className: String?): Boolean {
        val cls = className?.lowercase() ?: return false
        return config.infoWindows.any { cls.contains(it) }
    }

    /** שכבות 2–3: רכיבים במסך. מחזיר את הכלל שהתאים, או null. [info] — מסך פרטים (לפי שם החלון). */
    fun matchNodes(nodes: Sequence<Node>, info: Boolean = false): String? = inspect(nodes, info).rule

    /**
     * שכבות 2–3 + מסך פרטים: תמונת פרופיל גדולה על מסך שיש בו גם רשימה נגללת גדולה (או במסך פרטים לפי השם) —
     * חלק מהעמוד, לא צפייה; אלא אם היא ממלאת כמעט את כל המסך.
     */
    fun inspect(nodes: Sequence<Node>, info: Boolean = false): Verdict {
        var rule: String? = null
        var ruleArea = 0f
        var page = false
        for (n in nodes) {
            if (n.scrollable && n.areaFraction >= PAGE_AREA && !isImage(n)) {
                page = true
                if (rule != null) break
                continue
            }
            if (rule != null || n.areaFraction < config.minArea) continue
            match(n)?.let {
                rule = it
                ruleArea = n.areaFraction
                if (page) break
            }
        }
        val found = rule ?: return Verdict(null)
        if ((info || page) && ruleArea < config.infoMinArea) return Verdict(null, infoPhoto = true)
        return Verdict(found)
    }

    private fun match(n: Node): String? {
        val image = isImage(n)
        val id = n.viewId?.substringAfter(":id/", n.viewId)?.lowercase()
        if (id != null && (image || id.contains("photo") || id.contains("picture"))) {
            config.viewIds.firstOrNull { id == it || id.endsWith("_$it") || id.startsWith("${it}_") }
                ?.let { return "view_id:$it" }
        }
        if (image) {
            val desc = n.description?.lowercase() ?: return null
            config.descriptions.firstOrNull { desc.contains(it) }?.let { return "description" }
        }
        return null
    }

    private fun isImage(n: Node): Boolean {
        val c = n.className ?: return false
        return c.endsWith("ImageView") || c.endsWith("ImageButton") || c.contains("Photo", ignoreCase = true)
    }

    companion object {
        /** רשימה נגללת בגודל כזה (חלק מהמסך) — המסך הוא עמוד (פרטים), לא צפייה בתמונה. */
        const val PAGE_AREA = 0.25f
    }
}
