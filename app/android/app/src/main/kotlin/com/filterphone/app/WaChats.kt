package com.filterphone.app

import org.json.JSONObject

/**
 * באיזה סוג שיחה המשתמש נמצא בוואטסאפ: צ'אט פרטי, קבוצה או ערוץ — לפי תוכן המסך, בלי תלות בשם המסך
 * (ערוץ שנפתח מקישור, או גרסת וואטסאפ שפותחת ערוצים ושיחות באותו מסך, לא נתפסים לפי שם המסך).
 *
 * מה נקרא: מזהי רכיבים (viewIdResourceName), וטקסט קצר רק בכותרת השיחה (שם + שורת המשנה) ובפס הטאבים
 * בתחתית מסך הבית — לא הודעות. הסימנים מגיעים מהשרת ("wa_chat" במדיניות), כך שכשוואטסאפ משנה משהו
 * מעדכנים בשרת בלי גרסה חדשה; בלי השדה — ברירות המחדל כאן.
 *
 * טהור — נבדק ביחידה.
 */
object WaChats {
    enum class Kind { HOME, PRIVATE, GROUP, CHANNEL, UNKNOWN }

    data class Node(val viewId: String?, val text: String?, val description: String?, val top: Int, val bottom: Int,
                    val editable: Boolean = false)

    data class Signals(
        /** מזהה רכיב שמופיע רק בערוץ (בקוד של וואטסאפ ערוץ = "newsletter"). */
        val channelIds: List<String>,
        /** שורת המשנה בכותרת של ערוץ: "12K עוקבים" — מילה + ספרה. */
        val followerWords: List<String>,
        /** טקסט מדויק בכותרת שמסמן ערוץ ("ערוץ", כפתור "עקוב"). */
        val channelExact: List<String>,
        /** מזהה רכיב שמופיע רק בקבוצה (שם השולח מעל הודעה — name_in_group). */
        val groupIds: List<String>,
        /** טקסט בכותרת של קבוצה ("הקש/י כאן למידע על הקבוצה"). */
        val groupTexts: List<String>,
        /** "את/ה" ברשימת המשתתפים שבכותרת קבוצה ("משה, דוד, את/ה"). */
        val youWords: List<String>,
        /** מזהה רכיב של מסך שיחה (כותרת השיחה / שורת הכתיבה). */
        val conversationIds: List<String>,
    )

    val DEFAULT = Signals(
        channelIds = listOf("newsletter"),
        followerWords = listOf("עוקבים", "עוקבות", "followers", "follower"),
        channelExact = listOf("ערוץ", "channel", "עקוב", "follow"),
        groupIds = listOf("name_in_group"),
        groupTexts = listOf("מידע על הקבוצה", "למידע על הקבוצה", "group info", "פרטי הקבוצה"),
        youWords = listOf("את/ה", "אתה", "את", "you"),
        conversationIds = listOf("conversation_contact", "conversation_title", "conversation_header", ":id/entry"),
    )

    /** הכותרת: החלק העליון של המסך (שם השיחה ושורת המשנה). */
    const val HEADER_FRACTION = 0.2
    /** פס הטאבים של מסך הבית (צ'אטים / עדכונים / קהילות / שיחות) — בתחתית. */
    const val TABS_FRACTION = 0.75

    /** הסימנים מהשרת; שדה חסר / ריק — ברירת המחדל שלו. */
    fun parse(o: JSONObject?): Signals {
        if (o == null) return DEFAULT
        fun list(key: String, def: List<String>): List<String> =
            o.optJSONArray(key)?.let { a ->
                (0 until a.length()).map { a.optString(it).trim().lowercase() }.filter { it.length in 1..60 }
            }?.takeIf { it.isNotEmpty() } ?: def
        return Signals(
            channelIds = list("channel_ids", DEFAULT.channelIds),
            followerWords = list("follower_words", DEFAULT.followerWords),
            channelExact = list("channel_exact", DEFAULT.channelExact),
            groupIds = list("group_ids", DEFAULT.groupIds),
            groupTexts = list("group_texts", DEFAULT.groupTexts),
            youWords = list("you_words", DEFAULT.youWords),
            conversationIds = list("conversation_ids", DEFAULT.conversationIds),
        )
    }

    fun classify(nodes: List<Node>, screenHeight: Int, s: Signals = DEFAULT): Kind {
        if (screenHeight <= 0) return Kind.UNKNOWN
        val headerBottom = (screenHeight * HEADER_FRACTION).toInt()
        val tabsTop = (screenHeight * TABS_FRACTION).toInt()
        // מסך הבית (רשימת הצ'אטים / טאב העדכונים): פס הטאבים בתחתית — כאן לא "בתוך" שיחה. טאב העדכונים
        // מטופל בנפרד (WaScreens / guardWaUpdates) — גם בו מופיעים "עוקבים" ליד ערוצים מוצעים.
        val home = nodes.any { n ->
            n.top >= tabsTop && (WaScreens.isChatsTab(n.text, n.description) || WaScreens.isUpdatesLabel(n.text, n.description))
        }
        if (home) return Kind.HOME
        val ids = nodes.mapNotNull { it.viewId?.lowercase() }
        val headerNodes = nodes.filter { it.bottom in 1..headerBottom }
        val headerIds = headerNodes.mapNotNull { it.viewId?.lowercase() }
        val header = headerNodes
            .flatMap { listOfNotNull(it.text, it.description) }
            .map { UiRules.norm(it) }
            .filter { it.isNotEmpty() && it.length <= 240 }
        val canWrite = nodes.any { it.editable }
        fun channelId(id: String) = s.channelIds.any { id.contains(it) }
        // ערוץ: מזהה "ערוץ" בכותרת, או "12K עוקבים" / "ערוץ" / "עקוב" בכותרת. מזהה "ערוץ" במקום אחר במסך
        // נחשב רק כשאין שורת כתיבה — בצ'אט רגיל עם הודעה שהועברה מערוץ ("צפייה בערוץ") יש שורת כתיבה.
        if (headerIds.any(::channelId) || header.any { t -> isFollowers(t, s) || t in s.channelExact } ||
            (!canWrite && ids.any(::channelId))) return Kind.CHANNEL
        if (ids.any { id -> s.groupIds.any { id.contains(it) } } ||
            header.any { t -> s.groupTexts.any { t.contains(it) } || isParticipants(t, s) }) return Kind.GROUP
        if (ids.any { id -> s.conversationIds.any { id.contains(it) } }) return Kind.PRIVATE
        return Kind.UNKNOWN
    }

    /** "12.3K עוקבים" / "1,204 followers" / "ערוץ · 3 אלף עוקבים": ספרה + מילת "עוקבים". */
    fun isFollowers(text: String, s: Signals = DEFAULT): Boolean =
        text.any { it.isDigit() } && s.followerWords.any { w -> Regex("(^|[\\s·•,])${Regex.escape(w)}($|[\\s·•,])").containsMatchIn(text) }

    /**
     * רשימת משתתפים בכותרת קבוצה: "משה, דוד, את/ה" — שני פריטים ואחד מהם "את/ה" / "you", או שלושה פריטים
     * ומעלה (בצ'אט פרטי שורת המשנה היא "מחובר/ת" / "נראה/תה לאחרונה..." — בלי פסיקים).
     */
    fun isParticipants(text: String, s: Signals = DEFAULT): Boolean {
        if (!text.contains(',')) return false
        val parts = text.split(',').map { it.trim().trimEnd('…', '.') }.filter { it.isNotEmpty() }
        return parts.size >= 3 || (parts.size >= 2 && parts.any { it in s.youWords })
    }
}
