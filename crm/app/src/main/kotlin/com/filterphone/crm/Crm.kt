package com.filterphone.crm

import android.content.Context

/** הגדרות קבועות ומצב קטן (סמן ההתראות, הכניסה) — משותף למסך, לשירות ולמקלטים. */
object Crm {
    const val BASE = "https://api.filterphone.com"
    const val HOST = "api.filterphone.com"
    const val TAG = "CrmFilterPhone"

    /** כל כמה זמן השירות בודק בשרת (שניות). */
    const val POLL_SECONDS = 60L

    private const val PREFS = "crm_state"
    private const val K_TOKEN = "admin_token"
    private const val K_CURSOR = "cursor"
    private const val K_ASKED_BATTERY = "asked_battery"
    private const val K_RELOGIN_SHOWN = "relogin_shown"

    private fun prefs(c: Context) = c.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    // הכניסה (טוקן המנהל, 30 יום) — מוצפנת ב-Android Keystore
    fun token(c: Context): String? = SecureStore(c.applicationContext).get(K_TOKEN)?.takeIf { it.isNotEmpty() }

    fun setToken(c: Context, token: String?) {
        SecureStore(c.applicationContext).put(K_TOKEN, token)
        prefs(c).edit().remove(K_CURSOR).remove(K_RELOGIN_SHOWN).apply()
    }

    fun cursor(c: Context): String? = prefs(c).getString(K_CURSOR, null)
    fun setCursor(c: Context, v: String) = prefs(c).edit().putString(K_CURSOR, v).apply()

    fun askedBattery(c: Context): Boolean = prefs(c).getBoolean(K_ASKED_BATTERY, false)
    fun setAskedBattery(c: Context) = prefs(c).edit().putBoolean(K_ASKED_BATTERY, true).apply()

    fun reloginShown(c: Context): Boolean = prefs(c).getBoolean(K_RELOGIN_SHOWN, false)
    fun setReloginShown(c: Context, v: Boolean) = prefs(c).edit().putBoolean(K_RELOGIN_SHOWN, v).apply()

    /** טוקן מהדשבורד: אורך סביר ותווים בטוחים בלבד (token_urlsafe בשרת). טהור. */
    fun validToken(t: String?): Boolean =
        t != null && t.length in 16..256 && t.all { it in 'a'..'z' || it in 'A'..'Z' || it in '0'..'9' || it in "-_." }

    /** נתיב בדשבורד מתוך התראה ("#/support/7") — רק צורה מוכרת. טהור. */
    fun safeRoute(route: String?): String? =
        route?.takeIf { Regex("^#/[a-z]{2,20}(/[0-9]{1,9})?$").matches(it) }

    fun dashboardUrl(route: String? = null): String = "$BASE/admin/?app=1" + (safeRoute(route) ?: "")
}
