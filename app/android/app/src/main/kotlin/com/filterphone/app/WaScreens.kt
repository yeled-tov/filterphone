package com.filterphone.app

/**
 * חסימת טאב "עדכונים" בוואטסאפ (סטטוסים + ערוצים) — לפי תוכן המסך, כי מעבר בין הטאבים התחתונים
 * (צ'אטים / עדכונים / קהילות / שיחות) לא פותח מסך חדש באנדרואיד: זה אותו מסך, רק הטאב הנבחר משתנה.
 *
 * הסימן: רכיב שמסומן "נבחר" (isSelected) והשם שלו הוא הטאב "עדכונים" (או "סטטוס" בגרסאות ישנות
 * שבהן הסטטוסים היו טאב עליון). כשזה קורה — המגן לוחץ על הטאב "צ'אטים" (או "אחורה").
 * פתיחת סטטוס / ערוץ עצמם — מסכים נפרדים (StatusPlayback / Newsletter), נחסמים לפי שם המסך במדיניות.
 *
 * טהור — נבדק ביחידה. לא קורא הודעות: רק את שמות הטאבים ואת הסימון "נבחר".
 */
object WaScreens {
    val PACKAGES = setOf("com.whatsapp", "com.whatsapp.w4b")

    /** הטאב שבו נמצאים הסטטוסים והערוצים — עברית/אנגלית, וגם "סטטוס" של גרסאות ישנות. */
    private val UPDATES = listOf("עדכונים", "updates", "סטטוס", "status")

    /** הטאב "צ'אטים" (לשם מחזירים). לא "שיחות" — בעברית זה טאב השיחות הקוליות. */
    private val CHATS = listOf("צאטים", "chats")

    data class Node(val text: String?, val description: String?, val selected: Boolean)

    /** האם טאב העדכונים הוא הנבחר עכשיו. */
    fun updatesSelected(nodes: Sequence<Node>): Boolean =
        nodes.any { n -> n.selected && isUpdatesLabel(n.text, n.description) }

    /** השם של הרכיב (או של הכיתוב שבתוכו) הוא הטאב "עדכונים". */
    fun isUpdatesLabel(text: String?, description: String?): Boolean =
        isLabel(text, UPDATES) || isLabel(description, UPDATES)

    /** רכיב הוא הטאב "צ'אטים". */
    fun isChatsTab(text: String?, description: String?): Boolean =
        isLabel(text, CHATS) || isLabel(description, CHATS)

    /**
     * השם הוא אחד הטאבים: בדיוק המילה, או המילה ואחריה מונה ("עדכונים, 3 חדשים" / "Updates 2").
     * לא "מכיל" ולא "מתחיל ב" — כדי שצ'אט בשם "עדכונים מהגבאי" לא ייתפס בטעות.
     */
    private fun isLabel(raw: String?, labels: List<String>): Boolean {
        val s = norm(raw ?: return false)
        if (s.isEmpty()) return false
        return labels.any { l -> s == l || s.startsWith("$l,") || Regex("^${Regex.escape(l)} \\d").containsMatchIn(s) }
    }

    /** אותיות קטנות, בלי סימני כיוון, בלי גרש/גרשיים (צ'אטים / צ׳אטים / צאטים). */
    fun norm(s: String): String = s.replace(Regex("[\\u200e\\u200f\\u202a-\\u202e'׳\"״`]"), "")
        .replace(Regex("\\s+"), " ").trim().lowercase()
}
