package com.filterphone.app

import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.util.Log
import com.wireguard.android.backend.Tunnel

/**
 * הסייר המוגן הוא חלק מ-FilterPhone, לא אפליקציה עצמאית: אותה התקנה (בלי FilterPhone אין סייר), ועם סמל
 * משלו במסך הבית ובאחרונים. הוא עובד רק כשההגנה של FilterPhone באמת פעילה בטלפון:
 *   • הטלפון הופעל (יש הגדרות חיבור מהשרת) — לא סתם התקנה;
 *   • אין השהיה שהמנהל אישר;
 *   • הסינון פועל — המנהרה של FilterPhone מחוברת, או רשת כשרה (נטפרי וכו') שמסננת במקומה (KosherNet).
 * אחרת — מסך "הסייר פועל רק כש-FilterPhone מגן על הטלפון", בלי גלישה. כך אי אפשר להשתמש בו בלי סינון
 * (ובלי מנוי): מכשיר שהוסר מהשרת מאבד את החיבור — והסייר ננעל.
 */
object BrowserGate {
    enum class Block { OFF, NOT_SET_UP, PAUSED, FILTER_OFF }

    /** למה הסייר נעול עכשיו, או null — אפשר לגלוש. טהור (נבדק ביחידה). */
    fun decide(mode: String, configured: Boolean, paused: Boolean, tunnelUp: Boolean, kosherNet: Boolean): Block? = when {
        mode == SafeSites.OFF -> Block.OFF
        !configured -> Block.NOT_SET_UP
        paused -> Block.PAUSED
        !tunnelUp && !kosherNet -> Block.FILTER_OFF
        else -> null
    }

    fun check(context: Context): Block? {
        val app = context.applicationContext
        if (!TunnelManager.isInitialized) TunnelManager.init(app)
        return decide(
            mode = BrowserActivity.currentMode(app),
            configured = runCatching { TunnelManager.hasConfig() }.getOrDefault(false),
            paused = AdminState.isPaused(app),
            tunnelUp = TunnelManager.state == Tunnel.State.UP || ourVpnActive(app),
            kosherNet = runCatching { KosherNet.isActive(app) }.getOrDefault(false),
        )
    }

    /**
     * גיבוי לבדיקת המנהרה (אם המצב בזיכרון עוד לא התעדכן אחרי הפעלה מחדש של התהליך): המערכת מדווחת שהתעבורה
     * של האפליקציה עוברת ב-VPN, וה-VPN הקבוע ("תמיד מופעל") הוא של FilterPhone.
     */
    private fun ourVpnActive(context: Context): Boolean = runCatching {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return false
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) && TunnelManager.alwaysOn() == true
    }.getOrDefault(false)

    /**
     * הסמל של הסייר במסך הבית (activity-alias): מופיע רק כשהסייר פעיל בטלפון (מצב full / essential) ו-FilterPhone
     * הופעל. בהשהיה / חיבור שנפל הסמל נשאר (הסייר יסביר שהוא נעול) — לא מעלימים ומחזירים כל רגע.
     */
    fun syncLauncher(context: Context) {
        val app = context.applicationContext
        val show = runCatching {
            if (!TunnelManager.isInitialized) TunnelManager.init(app)
            BrowserActivity.currentMode(app) != SafeSites.OFF && TunnelManager.hasConfig()
        }.getOrDefault(false)
        val state = if (show) PackageManager.COMPONENT_ENABLED_STATE_ENABLED else PackageManager.COMPONENT_ENABLED_STATE_DISABLED
        val component = ComponentName(app, LAUNCHER_ALIAS)
        runCatching {
            if (app.packageManager.getComponentEnabledSetting(component) != state) {
                app.packageManager.setComponentEnabledSetting(component, state, PackageManager.DONT_KILL_APP)
            }
        }.onFailure { Log.w("FilterPhone", "safe browser launcher toggle failed", it) }
    }

    const val LAUNCHER_ALIAS = "com.filterphone.app.SafeBrowserLauncher"
}
