package com.filterphone.app

/**
 * תמונות פרופיל בוואטסאפ — "לבן בכל מקום": אילו רכיבים במסך הם תמונת פרופיל (רשימת הצ'אטים, כותרת
 * השיחה, פרטי איש קשר / קבוצה, משתתפים, שיחות, בחירת איש קשר), ומה לכסות.
 *
 * חסימה ברשת (pps.whatsapp.net) עוצרת רק תמונות חדשות — מה שכבר שמור בטלפון וואטסאפ מציג מהזיכרון.
 * לכן מכסים את המקום עצמו במסך (AvatarMask), בלי קשר למקור התמונה.
 *
 *   • תמונה אחת — עיגול לבן מעליה (קצת גדול ממנה).
 *   • בזמן גלילה של רשימה — פס לבן לכל גובה הרשימה בעמודה של התמונות, כדי שלא "תציץ" תמונה בין עדכון
 *     לעדכון. כשהגלילה נעצרת — חוזרים לעיגולים (פס קבוע היה מסתיר גם כפתורים שבאותה עמודה).
 *   • תמונה גדולה (צפייה בתמונת פרופיל) — לא כאן: WaProfileDetector יוצא מהמסך.
 *
 * הזיהוי: מזהה הרכיב (view id) מתוך רשימה שמגיעה מהשרת, או תיאור נגישות של רכיב תמונה ("תמונת פרופיל"),
 * ורק רכיב כמעט ריבועי (תמונות בצ'אט / תצוגות קישור לא מכוסות). פרטיות: רק מזהה, סוג, תיאור של תמונה ומיקום —
 * בלי טקסט של הודעות. בלי תלות באנדרואיד — נבדק ביחידה.
 */
object WaAvatars {
    data class Box(val left: Int, val top: Int, val right: Int, val bottom: Int, val round: Boolean = true) {
        val width: Int get() = right - left
        val height: Int get() = bottom - top
    }

    /** רכיב מהמסך: מזהה, סוג, תיאור (רק ברכיבי תמונה), מיקום, והגבולות של הרשימה הנגללת שהוא בתוכה (אם יש). */
    data class Node(val viewId: String?, val className: String?, val description: String?, val box: Box,
                    val list: Box? = null)

    data class Config(
        val ids: List<String> = DEFAULT_IDS,
        val descriptions: List<String> = DEFAULT_DESCRIPTIONS,
        val maxFraction: Float = 0.3f,
    )

    /** מזהי רכיבים של תמונות פרופיל בגרסאות וואטסאפ שונות (מספיק שהמזהה מכיל אחד מהם). */
    val DEFAULT_IDS = listOf(
        "contact_photo", "profile_picture", "profile_photo", "wds_profile_picture", "contactpicker_row_photo",
        "contact_row_photo", "avatar", "group_photo", "participant_photo", "status_contact_photo", "chat_photo",
        "community_photo", "subgroup_photo", "newsletter_photo", "channel_photo", "contact_icon", "photo_image",
    )
    val DEFAULT_DESCRIPTIONS = listOf("profile photo", "profile picture", "תמונת פרופיל", "תמונת הפרופיל",
        "foto de perfil", "photo de profil", "profilbild")

    private fun isImage(cls: String?): Boolean =
        cls != null && (cls.endsWith("ImageView") || cls.endsWith("ImageButton") || cls.contains("Photo", true) ||
            cls.contains("Avatar", true))

    /** תמונת פרופיל? [minPx] — גודל מינימלי (קטן מזה — אייקון / נקודה); [screenArea] — לשטח המקסימלי. */
    fun isAvatar(n: Node, cfg: Config, screenArea: Long, minPx: Int): Boolean {
        val w = n.box.width
        val h = n.box.height
        if (w < minPx || h < minPx) return false
        val ratio = w.toFloat() / h
        if (ratio < 0.7f || ratio > 1.43f) return false                 // לא ריבועי — לא תמונת פרופיל
        if (w.toLong() * h > screenArea * cfg.maxFraction) return false  // צפייה בתמונה גדולה — WaProfileDetector
        val id = n.viewId?.substringAfter(":id/")?.lowercase()
        if (id != null && cfg.ids.any { id == it || id.contains(it) }) return true
        val desc = n.description?.lowercase() ?: return false
        return isImage(n.className) && cfg.descriptions.any { desc.contains(it) }
    }

    /**
     * מה לכסות. [scrolling] — רשימה בגלילה עכשיו: פס לכל גובה הרשימה לתמונות קטנות ([stripMaxPx]) שבתוך רשימה.
     * [pad] — כמה להגדיל כל עיגול (שוליים של התמונה / טבעת סטטוס).
     */
    fun masks(nodes: Sequence<Node>, cfg: Config, screenArea: Long, minPx: Int, pad: Int, scrolling: Boolean,
              stripMaxPx: Int): List<Box> {
        val out = LinkedHashSet<Box>()
        for (n in nodes) {
            if (!isAvatar(n, cfg, screenArea, minPx)) continue
            val b = n.box
            val list = n.list
            out += if (scrolling && list != null && b.width <= stripMaxPx) {
                // עמודה מעוגלת ל-4 פיקסלים — כל השורות של אותה רשימה נותנות אותו פס (בלי כפילויות)
                Box(snap(b.left - pad), list.top, snap(b.right + pad + 3), list.bottom, round = false)
            } else {
                Box(b.left - pad, b.top - pad, b.right + pad, b.bottom + pad)
            }
        }
        // עיגול שכבר בתוך פס — מיותר
        val strips = out.filter { !it.round }
        return out.filter { box -> box.round.not() || strips.none { s -> s.left <= box.left && s.right >= box.right &&
            s.top <= box.top && s.bottom >= box.bottom } }
    }

    private fun snap(v: Int): Int = v - Math.floorMod(v, 4)

    /** מזהים של רכיבי תמונה כמעט ריבועיים שנראו בוואטסאפ (לכיול מרחוק — רק מזהים, בלי תוכן). */
    fun squareImageIds(nodes: Sequence<Node>, minPx: Int, max: Int = 30): Set<String> {
        val out = LinkedHashSet<String>()
        for (n in nodes) {
            if (out.size >= max) break
            val id = n.viewId?.substringAfter(":id/") ?: continue
            if (!isImage(n.className) || n.box.width < minPx || n.box.height < minPx) continue
            val ratio = n.box.width.toFloat() / n.box.height
            if (ratio in 0.7f..1.43f) out += id
        }
        return out
    }
}
