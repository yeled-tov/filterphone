package com.filterphone.app

import android.content.Context
import org.json.JSONObject

/**
 * פקודת מנהל שהגיעה בערוץ המאומת (תשובת heartbeat): השהיית הגנה (מצב תחזוקה) או הסרת ניהול.
 * נשמרת מקומית כדי שהמגן ומנהל המכשיר יכבדו אותה גם בין דיווחים, ומשוחזרת לבד בפקיעת התוקף.
 *
 * אין כאן "מעקף לקוח": הפקודה מגיעה רק מהשרת (אחרי אישור מנהל, מעל HTTPS עם אסימון המכשיר),
 * עם nonce ל-anti-replay ותוקף. הסרת הניהול מתבצעת רק כשהשרת מבקש זאת במפורש.
 */
object AdminState {
    private const val PREFS = "fp_admin"
    private const val K_PAUSE_UNTIL = "pause_until"      // מתי מסתיימת ההשהיה (אפוק מילישניות מקומי)
    private const val K_PAUSE_NONCE = "pause_nonce"

    /** תוצאת החלת פקודה — כדי ש-Sync ידע אם לבצע פעולה (למשל הסרת ניהול). */
    enum class Action { NONE, PAUSED, UNMANAGE }

    /**
     * מחיל את בלוק ה-"admin" מתשובת ה-heartbeat. מחזיר מה צריך לקרות עכשיו.
     * [serverUntil] הוא זמן שרת (שניות); ממירים למונוטוני מקומי לפי הפרש "עכשיו" של השרת.
     */
    fun apply(context: Context, admin: JSONObject?, serverTimeS: Long): Action {
        val p = prefs(context)
        if (admin == null) {
            if (p.contains(K_PAUSE_UNTIL)) p.edit().remove(K_PAUSE_UNTIL).remove(K_PAUSE_NONCE).apply()
            return Action.NONE
        }
        return when (admin.optString("action")) {
            "unmanage" -> Action.UNMANAGE
            "pause" -> {
                val nonce = admin.optString("nonce")
                // תוקף מקומי = עכשיו + (until − זמן השרת). כך אין תלות בשעון המכשיר.
                val remainMs = remainMs(admin.optLong("until", 0), serverTimeS)
                p.edit().putLong(K_PAUSE_UNTIL, System.currentTimeMillis() + remainMs)
                    .putString(K_PAUSE_NONCE, nonce).apply()
                Action.PAUSED
            }
            else -> Action.NONE
        }
    }

    /** ההגנה מושהית כרגע (מצב תחזוקה) — המגן ונעילת ה-VPN מורפים. */
    fun isPaused(context: Context): Boolean {
        val until = prefs(context).getLong(K_PAUSE_UNTIL, 0)
        if (until == 0L) return false
        if (System.currentTimeMillis() >= until) {       // פג — מנקים ומחזירים הגנה
            clear(context)
            return false
        }
        return true
    }

    fun pauseUntil(context: Context): Long = prefs(context).getLong(K_PAUSE_UNTIL, 0)

    fun clear(context: Context) = prefs(context).edit().remove(K_PAUSE_UNTIL).remove(K_PAUSE_NONCE).apply()

    /** כמה מילישניות נשארו להשהיה, לפי זמן השרת (בלי תלות בשעון המכשיר). טהור — נבדק ביחידה. */
    fun remainMs(untilServerS: Long, serverNowS: Long): Long =
        (untilServerS - serverNowS).coerceAtLeast(0) * 1000

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
