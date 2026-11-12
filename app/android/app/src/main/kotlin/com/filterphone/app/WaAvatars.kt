package com.filterphone.app

/**
 * תמונות פרופיל בוואטסאפ — "לבן בכל מקום": אילו רכיבים במסך הם תמונת פרופיל (רשימת הצ'אטים, כותרת
 * השיחה, פרטי איש קשר / קבוצה, משתתפים, שיחות, בחירת איש קשר, ליד הודעה קולית), ומה לכסות.
 *
 * חסימה ברשת (pps.whatsapp.net) עוצרת רק תמונות חדשות — מה שכבר שמור בטלפון וואטסאפ מציג מהזיכרון.
 * לכן מכסים את המקום עצמו במסך (AvatarMask), בלי קשר למקור התמונה.
 *
 *   • תמונה אחת — עיגול לבן מעליה (קצת גדול ממנה). תמונה שנחתכה בקצה הרשימה (נכנסת / יוצאת מהמסך) —
 *     העיגול המלא, חתוך לגבולות הרשימה: "מחליק" החוצה יחד עם התמונה ולא הופך לאליפסה.
 *   • בגלילה — העיגולים זזים עם הרשימה (AvatarMask מנבא את המיקום בין מדידה למדידה לפי המהירות, [shift]).
 *     רק בגלילה מהירה מאוד ("זריקה") — פס לבן לכל גובה הרשימה בעמודה של התמונות.
 *   • תמונה גדולה (צפייה בתמונת פרופיל) — לא כאן: WaProfileDetector יוצא מהמסך. במסך פרטי איש קשר / קבוצה
 *     ([Config.bannerMinWidth]) — גם התמונה הגדולה בראש העמוד מכוסה (מלבן), ונשארים במסך.
 *
 * הזיהוי: מזהה הרכיב (view id) מתוך רשימה שמגיעה מהשרת, או תיאור נגישות של רכיב תמונה ("תמונת פרופיל"),
 * ורק רכיב כמעט ריבועי (תמונות בצ'אט / תצוגות קישור לא מכוסות). פרטיות: רק מזהה, סוג, תיאור של תמונה ומיקום —
 * בלי טקסט של הודעות. בלי תלות באנדרואיד — נבדק ביחידה.
 */
object WaAvatars {
    /**
     * מה לצייר: [round] — עיגול (אחרת מלבן); [clip] — לא לצייר מחוץ לגבולות האלה (הרשימה); [follow] — זז עם
     * הגלילה של הרשימה (הניבוי של AvatarMask).
     */
    data class Box(val left: Int, val top: Int, val right: Int, val bottom: Int, val round: Boolean = true,
                   val clip: Box? = null, val follow: Boolean = false) {
        val width: Int get() = right - left
        val height: Int get() = bottom - top
    }

    /**
     * רכיב מהמסך: מזהה, סוג, תיאור (רק ברכיבי תמונה), מיקום, הגבולות של הרשימה הנגללת שהוא בתוכה (אם יש),
     * ו-[key] — זהות הרכיב בין מדידות (אותו רכיב באותה שורה), לחישוב מהירות הגלילה. 0 = לא ידוע.
     */
    data class Node(val viewId: String?, val className: String?, val description: String?, val box: Box,
                    val list: Box? = null, val key: Int = 0)

    data class Config(
        val ids: List<String> = DEFAULT_IDS,
        val descriptions: List<String> = DEFAULT_DESCRIPTIONS,
        val maxFraction: Float = 0.3f,
        /** במסך פרטים: תמונה רחבה לפחות כך (פיקסלים) שזוהתה כתמונת פרופיל — מכוסה גם אם אינה ריבועית
         *  (התמונה בראש העמוד מתכווצת בגלילה). 0 = כבוי. */
        val bannerMinWidth: Int = 0,
    )

    /** מזהי רכיבים של תמונות פרופיל בגרסאות וואטסאפ שונות (מספיק שהמזהה מכיל אחד מהם). */
    val DEFAULT_IDS = listOf(
        "contact_photo", "profile_picture", "profile_photo", "wds_profile_picture", "contactpicker_row_photo",
        "contact_row_photo", "avatar", "group_photo", "participant_photo", "status_contact_photo", "chat_photo",
        "community_photo", "subgroup_photo", "newsletter_photo", "channel_photo", "contact_icon", "photo_image",
    )
    val DEFAULT_DESCRIPTIONS = listOf("profile photo", "profile picture", "תמונת פרופיל", "תמונת הפרופיל",
        "foto de perfil", "photo de profil", "profilbild")

    /** תמונה גדולה מזה (חלק מהמסך) — מכוסה במלבן ולא בעיגול (התמונה בראש מסך הפרטים). */
    private const val BIG_FRACTION = 0.08f

    private fun isImage(cls: String?): Boolean =
        cls != null && (cls.endsWith("ImageView") || cls.endsWith("ImageButton") || cls.contains("Photo", true) ||
            cls.contains("Avatar", true))

    private fun matches(n: Node, cfg: Config): Boolean {
        val id = n.viewId?.substringAfter(":id/")?.lowercase()
        if (id != null && cfg.ids.any { id == it || id.contains(it) }) return true
        val desc = n.description?.lowercase() ?: return false
        return isImage(n.className) && cfg.descriptions.any { desc.contains(it) }
    }

    /**
     * המיקום המלא של תמונה קטנה ([maxPx]) שנחתכה בקצה הרשימה: אנדרואיד מדווח רק את החלק הגלוי, אז משלימים
     * לריבוע לפי הצלע שלא נחתכה, בצד שיצא מהרשימה. תמונה שלא נחתכה — כמו שהיא.
     */
    fun fullBox(n: Node, maxPx: Int): Box {
        val b = n.box
        val list = n.list ?: return b
        val cutV = b.height < b.width - 1 && b.width <= maxPx          // נחתכה למעלה / למטה (הרוחב אמיתי)
        val cutH = b.width < b.height - 1 && b.height <= maxPx         // נחתכה בצד (מעבר טאב)
        return when {
            cutV && b.top <= list.top + 1 -> Box(b.left, b.bottom - b.width, b.right, b.bottom)
            cutV && b.bottom >= list.bottom - 1 -> Box(b.left, b.top, b.right, b.top + b.width)
            cutH && b.left <= list.left + 1 -> Box(b.right - b.height, b.top, b.right, b.bottom)
            cutH && b.right >= list.right - 1 -> Box(b.left, b.top, b.left + b.height, b.bottom)
            else -> b
        }
    }

    /**
     * תמונת פרופיל? [minPx] — גודל מינימלי (קטן מזה — אייקון / נקודה); [screenArea] — לשטח המקסימלי;
     * [maxPx] — תמונה קטנה ברשימה (מושלמת לריבוע כשנחתכה בקצה, [fullBox]).
     */
    fun isAvatar(n: Node, cfg: Config, screenArea: Long, minPx: Int, maxPx: Int = Int.MAX_VALUE): Boolean {
        val f = fullBox(n, maxPx)
        val w = f.width
        val h = f.height
        if (w < minPx || h < minPx) return false
        val ratio = w.toFloat() / h
        if (w.toLong() * h > screenArea * cfg.maxFraction) return false  // צפייה בתמונה גדולה — WaProfileDetector
        if (ratio < 0.7f || ratio > 1.43f) {                             // לא ריבועי — לא תמונת פרופיל
            return cfg.bannerMinWidth > 0 && w >= cfg.bannerMinWidth && matches(n, cfg)  // התמונה בראש מסך הפרטים
        }
        return matches(n, cfg)
    }

    /**
     * מה לכסות. [scrolling] — "זריקה" מהירה עכשיו: פס לכל גובה הרשימה לתמונות קטנות ([stripMaxPx]) שבתוך רשימה.
     * [pad] — כמה להגדיל כל עיגול (שוליים של התמונה / טבעת סטטוס).
     */
    fun masks(nodes: Sequence<Node>, cfg: Config, screenArea: Long, minPx: Int, pad: Int, scrolling: Boolean,
              stripMaxPx: Int): List<Box> {
        val out = LinkedHashSet<Box>()
        for (n in nodes) {
            if (!isAvatar(n, cfg, screenArea, minPx, stripMaxPx)) continue
            val b = fullBox(n, stripMaxPx)
            val list = n.list
            out += if (scrolling && list != null && b.width <= stripMaxPx) {
                // עמודה מעוגלת ל-4 פיקסלים — כל השורות של אותה רשימה נותנות אותו פס (בלי כפילויות)
                Box(snap(b.left - pad), list.top, snap(b.right + pad + 3), list.bottom, round = false)
            } else {
                val ratio = b.width.toFloat() / b.height.coerceAtLeast(1)
                val square = ratio in 0.7f..1.43f
                val big = b.width.toLong() * b.height > screenArea * BIG_FRACTION
                Box(b.left - pad, b.top - pad, b.right + pad, b.bottom + pad, round = square && !big,
                    clip = list, follow = list != null)
            }
        }
        // עיגול שכבר בתוך פס — מיותר
        val strips = out.filter { !it.round && it.clip == null && !it.follow }
        return out.filter { box -> box.round.not() || strips.none { s -> s.left <= box.left && s.right >= box.right &&
            s.top <= box.top && s.bottom >= box.bottom } }
    }

    /**
     * כמה הרשימה זזה בין שתי מדידות (dx, dy בפיקסלים): החציון של ההפרשים של אותם רכיבים ([Node.key]) שלא
     * נחתכו בקצה. אותו רכיב = אותה שורה בפרק זמן קצר (רשימות ממחזרות שורות רק כשהן יוצאות מהמסך).
     * null — אין אף רכיב משותף (המסך התחלף / כל השורות חדשות).
     */
    fun shift(prev: List<Node>, next: List<Node>): Pair<Int, Int>? {
        if (prev.isEmpty() || next.isEmpty()) return null
        val before = HashMap<Int, Box>()
        for (n in prev) if (n.key != 0 && whole(n)) before[n.key] = n.box
        val dx = ArrayList<Int>()
        val dy = ArrayList<Int>()
        for (n in next) {
            if (n.key == 0 || !whole(n)) continue
            val p = before[n.key] ?: continue
            if (p.width != n.box.width || p.height != n.box.height) continue      // רכיב שהשתנה — לא תזוזה
            dx += n.box.left - p.left
            dy += n.box.top - p.top
        }
        if (dy.isEmpty()) return null
        dx.sort()
        dy.sort()
        return dx[dx.size / 2] to dy[dy.size / 2]
    }

    /** רכיב שלא נחתך בקצה הרשימה (המיקום שלו אמיתי, לא רק החלק הגלוי). */
    private fun whole(n: Node): Boolean {
        val l = n.list ?: return true
        val b = n.box
        return b.top > l.top + 1 && b.bottom < l.bottom - 1 && b.left > l.left + 1 && b.right < l.right - 1
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
