package com.filterphone.app

import android.content.Context
import android.content.SharedPreferences

/**
 * מצב ההגנה שנשמר בין הפעלות של התהליך: בדיקות VPN, ניסיונות חיבור מחדש, סנכרון מדיניות,
 * דיווח לשרת, ומוני חסימות. רק מספרים וזמנים — בלי שום תוכן מהטלפון.
 */
object ProtectionState {
    private const val PREFS = "fp_state"

    const val VPN_HEALTH_OK = "vpn_health_ok_at"     // מתי לאחרונה המנהרה עבדה (handshake טרי)
    const val VPN_RECONNECTS = "vpn_reconnects"      // כמה פעמים חיברנו מחדש לבד
    const val VPN_ERROR = "vpn_error"
    const val VPN_DATA_CHECKED = "vpn_data_checked_at" // מתי לאחרונה נבדקה תעבורה אמיתית במנהרה (DataProbe)
    const val VPN_DATA_OK = "vpn_data_ok_at"         // מתי לאחרונה תעבורה עברה
    const val VPN_DATA_FAIL = "vpn_data_fail_at"     // מתי לאחרונה המנהרה הייתה למעלה בלי תעבורה
    const val VPN_DATA_ERROR = "vpn_data_error"      // באיזה שלב נכשלה (dns / connect / timeout / http:bad)
    const val VPN_DATA_FAILS = "vpn_data_fails"      // כמה בדיקות תעבורה נכשלו בסך הכול
    const val VPN_CONSENT_RESTORED = "vpn_consent_restored" // כמה פעמים מנהל המכשיר החזיר את ה-VPN שאנדרואיד ביטל
    const val WA_IMAGE_IDS = "wa_image_ids"          // מזהי רכיבי תמונה ריבועיים שנראו בוואטסאפ (לכיול הכיסוי)
    const val VPN_UNSTUCK = "vpn_unstuck"            // כמה פעמים שוחרר "מחובר בלי תעבורה" (באג אנדרואיד 16)
    const val POLICY_SYNC = "policy_sync_at"          // מתי לאחרונה קיבלנו / אישרנו מדיניות מהשרת
    const val HEARTBEAT_OK = "heartbeat_ok_at"
    const val HEARTBEAT_ERROR = "heartbeat_error"
    const val GUARD_ALIVE = "guard_alive_at"          // שירות הנגישות רץ (מתעדכן כשהוא מתחבר ובאירועים)
    const val GUARD_RESTORED = "guard_restored"        // כמה פעמים המגן כובה בהגדרות והוחזר לבד
    const val APPS_BLOCKED = "apps_blocked"
    const val SCREENS_BLOCKED = "screens_blocked"
    const val WA_PHOTOS_BLOCKED = "wa_photos_blocked"
    const val KEYWORDS_BLOCKED = "keywords_blocked"      // חיפושים שנחסמו לפי מילה
    const val GUARD_ERRORS = "guard_errors"            // תקלות במגן שנתפסו (לא הפילו את התהליך / ה-VPN)
    const val GUARD_ERROR = "guard_error"              // התקלה האחרונה (מקום + סוג) — לאבחון בשרת
    const val CA_PEM = "ca_pem"                       // תעודת הסינון (ציבורית) — לבדיקה אם מותקנת
    const val ORG_NAME = "org_name"                   // שם הארגון מהשרת ("המכשיר הזה שייך ל-…")
    const val WA_BROWSER = "wa_browser"               // הדפדפן הפנימי של וואטסאפ: איך הסתיימה ההעברה האחרונה ל-Chrome

    fun prefs(context: Context): SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun mark(context: Context, key: String, at: Long = System.currentTimeMillis()) =
        prefs(context).edit().putLong(key, at).apply()

    fun time(context: Context, key: String): Long = prefs(context).getLong(key, 0L)

    fun inc(context: Context, key: String) {
        val p = prefs(context)
        p.edit().putLong(key, p.getLong(key, 0L) + 1).apply()
    }

    fun count(context: Context, key: String): Long = prefs(context).getLong(key, 0L)

    fun setString(context: Context, key: String, value: String?) =
        prefs(context).edit().apply { if (value == null) remove(key) else putString(key, value) }.apply()

    fun string(context: Context, key: String): String? = prefs(context).getString(key, null)

    /** "הסרת הכול": מוני חסימות, זמנים והתעודה — נמחקים. */
    fun clearAll(context: Context) = prefs(context).edit().clear().apply()
}

/**
 * השהיה הולכת וגדלה בין ניסיונות (5 שניות, 15, 30, דקה ... עד 15 דקות), נשמרת בין הפעלות —
 * כך שתקלה מתמשכת לא יוצרת לולאה שמרוקנת את הסוללה. מתאפסת בהצלחה.
 */
class Backoff(
    private val prefs: SharedPreferences,
    private val name: String,
    private val steps: LongArray = longArrayOf(5_000, 15_000, 30_000, 60_000, 120_000, 300_000, 600_000, 900_000),
    private val clock: () -> Long = { System.currentTimeMillis() },
) {
    val attempts: Int get() = prefs.getInt("${name}_attempts", 0)
    private val notBefore: Long get() = prefs.getLong("${name}_not_before", 0L)

    /** האם מותר לנסות עכשיו. */
    fun ready(): Boolean = clock() >= notBefore

    /** כמה לחכות עד הניסיון הבא (0 = עכשיו). */
    fun waitMs(): Long = (notBefore - clock()).coerceAtLeast(0L)

    /** ניסיון נכשל: הניסיון הבא רק אחרי ההשהיה הבאה (לכל היותר [cap]). מחזיר את ההשהיה. */
    fun failed(cap: Long = Long.MAX_VALUE): Long {
        val n = attempts
        val delay = minOf(steps[n.coerceAtMost(steps.size - 1)], cap)
        prefs.edit().putInt("${name}_attempts", n + 1).putLong("${name}_not_before", clock() + delay).apply()
        return delay
    }

    /** מקצר המתנה ארוכה (למשל שנשמרה מלפני אתחול) ל-[cap] לכל היותר מעכשיו. */
    fun capWait(cap: Long) {
        if (notBefore - clock() > cap) prefs.edit().putLong("${name}_not_before", clock() + cap).apply()
    }

    fun succeeded() {
        if (attempts != 0 || notBefore != 0L) {
            prefs.edit().remove("${name}_attempts").remove("${name}_not_before").apply()
        }
    }
}
