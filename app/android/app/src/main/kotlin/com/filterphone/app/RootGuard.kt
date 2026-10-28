package com.filterphone.app

/**
 * כלי Root (מג'יסק וכו'): כשהמנהל מפעיל את האפשרות "כלי Root" (block_root_prompt במדיניות), המגן
 * חוסם לא רק את האפליקציות עצמן (כמו כל אפליקציה חסומה) אלא גם את *חלון בקשת הרשאת-העל* (su) —
 * החלון ש"מאשר לאפליקציה גישת-על". חסימת החלון = לחיצת "אחורה", כלומר דחיית הבקשה כברירת מחדל,
 * כך שאי אפשר להעניק גישת-על (ואיתה למחוק נתונים או לעקוף את הסינון).
 *
 * כאן רק הזיהוי (טהור, נבדק ביחידה) — מתי חלון הוא בקשת הרשאת-על. אין כאן שום קוד שמשיג / מנצל
 * הרשאת-על; ההפך — זיהוי של בקשה כזו כדי לדחות אותה. הפעולה עצמה (לחיצת "אחורה") ב-GuardService.
 */
object RootGuard {
    /** מנהלי הרשאת-על ואפליקציות שמשנות אפליקציות — החלון שלהן נבדק (מחוץ ל-FilterPhone עצמה). */
    val MANAGERS = setOf(
        "com.topjohnwu.magisk", "io.github.huskydg.magisk", "me.weishu.kernelsu", "me.bmax.apatch",
        "me.weishu.exp", "eu.chainfire.supersu", "com.koushikdutta.superuser", "com.kingroot.kinguser",
        "com.speedsoftware.rootexplorer", "com.mixplorer.silver", "bin.mt.plus", "bin.mt.plus.canary",
        "com.chelpus.lackypatch", "com.dimonvideo.luckypatcher", "com.forpda.lp",
    )

    private val CLASS = Regex("(surequest|superuser|su_?request|rootrequest|grantroot)", RegexOption.IGNORE_CASE)

    // ניסוחים חזקים של חלון בקשת הרשאת-על (עברית ואנגלית, אותיות קטנות).
    private val STRONG = listOf(
        "superuser request", "superuser access", "grant superuser", "requesting superuser", "requesting root",
        "su request", "root request", "wants to access superuser", "wants superuser",
        "בקשת הרשאת על", "בקשת הרשאת-על", "בקשת גישת על", "בקשת גישת-על", "בקשת סופר משתמש", "בקשת סופר-משתמש",
        "סופר משתמש", "סופר-משתמש", "מבקש הרשאת על", "מבקשת הרשאת על",
    )
    private val GRANT = listOf("grant", "allow", "אשר", "אישור", "הענק", "הענקת", "אפשר", "לאפשר")
    private val ROOT = listOf("superuser", "root access", "הרשאת על", "הרשאת-על", "גישת על", "גישת-על", "סופר משתמש")

    private val BIDI = Regex("[\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069]")
    private fun norm(s: String) = s.replace(BIDI, "").replace(Regex("\\s+"), " ").trim().lowercase()

    /** שם המסך (Activity / חלון) נראה כמו בקשת הרשאת-על — סינון זול לפני שסורקים את הטקסט. */
    fun looksLikeSuWindow(cls: String?): Boolean = cls != null && CLASS.containsMatchIn(cls)

    /**
     * האם [texts] הם חלון בקשת הרשאת-על: ניסוח חזק, או צירוף של "אשר/הענק" עם "הרשאת-על/superuser".
     * (בלי [cls] / [pkg] — בשביל הבדיקות; ב-GuardService משלבים גם אותם.)
     */
    fun looksLikeSuText(texts: List<String>): Boolean {
        val all = texts.map(::norm).filter { it.isNotEmpty() }
        if (all.any { t -> STRONG.any { t.contains(it) } }) return true
        val grant = all.any { t -> GRANT.any { t.contains(it) } }
        val root = all.any { t -> ROOT.any { t.contains(it) } }
        return grant && root
    }

    /**
     * חלון בקשת הרשאת-על שצריך לדחות. true כש:
     *   • זה לא FilterPhone עצמה, וגם
     *   • שם המסך נראה כמו בקשת su, או שהטקסט הוא בקשת su, או שזו אפליקציית מנהל Root והטקסט מאשר כזו.
     * כך פתיחת המנהל עצמו (בלי חלון אישור) *לא* נתפסת כאן — היא נחסמת כאפליקציה רגילה (אחורה→בית).
     */
    fun isSuPrompt(pkg: String?, cls: String?, texts: List<String>): Boolean {
        if (pkg == "com.filterphone.app") return false
        if (looksLikeSuWindow(cls)) return true
        return looksLikeSuText(texts)
    }
}
