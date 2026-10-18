package com.filterphone.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.os.PowerManager
import android.util.Log

/**
 * שומר: בודק שכל מה שההגנה צריכה עדיין במקום — ומחזיר לבד את מה שאפשר.
 *
 * רץ מהשירות הקדמי (הפעלה, כל הדלקת מסך, שינוי רשת) ומהמשימה התקופתית. בכל ריצה:
 *   1. מגן FilterPhone (נגישות) — מודלק מחדש מיד אם כובה (עם WRITE_SECURE_SETTINGS מההקמה).
 *   2. מנהל מכשיר — כל ההגבלות וה-VPN הנעול מוחלים מחדש (לכל היותר כל 10 דקות).
 *   3. ה-VPN עצמו — TunnelManager.healthCheck (נקרא לפני השומר).
 * מה שאנדרואיד לא מרשה להחזיר בלי המשתמש (אישור VPN שבוטל, מגן בלי ההרשאה, הגבלת סוללה) —
 * התראה בולטת שפותחת את FilterPhone ישר לחלון של מה שחסר. ההתראה יורדת לבד כשהכול תקין.
 * (רק אחרי שההגנה כבר הייתה שלמה פעם אחת — בהקמה הראשונה האפליקציה עצמה מובילה.)
 */
object Watchdog {
    private const val TAG = "FilterPhone"
    private const val PREFS = "fp_watchdog"
    private const val K_DPC_AT = "dpc_applied_at"
    private const val K_ALERTED = "alerted"            // מה כבר הוצג בהתראה (כדי לא להציק שוב ושוב)
    private const val K_ALERTED_AT = "alerted_at"
    private const val K_WAS_OK = "was_ok"              // ההגנה הייתה שלמה לפחות פעם אחת (אחרי ההקמה)
    private const val DPC_EVERY_MS = 10 * 60_000L
    private const val REALERT_MS = 6 * 3600_000L
    private const val CHANNEL_ID = "alerts"
    private const val NOTIFICATION_ID = 7302

    const val VPN = "vpn"
    const val GUARD = "guard"
    const val BATTERY = "battery"

    /**
     * המגן נדרש: מסכים / הגנות בוואטסאפ / מילות חיפוש / רשימה לבנה — תמיד; חסימת אפליקציות — רק בלי
     * מנהל מכשיר (שם הן מוסתרות).
     */
    fun guardRequired(context: Context): Boolean {
        val policy = GuardPolicy.get(context)
        return policy.screens.isNotEmpty() || policy.waProfile != null || policy.keywords != null ||
            policy.allowlistOnly || (policy.apps.isNotEmpty() && !DeviceOwnerManager.isDeviceOwner(context))
    }

    /** טהור: מה חסר, לפי המצב. נבדק ביחידה. */
    fun missing(vpnPermission: Boolean, guardNeeded: Boolean, guardOn: Boolean, batteryOk: Boolean?): List<String> =
        buildList {
            if (!vpnPermission) add(VPN)
            if (guardNeeded && !guardOn) add(GUARD)
            if (batteryOk == false) add(BATTERY)
        }

    /** טהור: להציג התראה עכשיו? כשמשהו חדש חסר, או אותו דבר עדיין חסר אחרי 6 שעות. */
    fun shouldAlert(missing: List<String>, alerted: String, alertedAt: Long, now: Long): Boolean =
        missing.isNotEmpty() && (missing.joinToString(",") != alerted || now - alertedAt > REALERT_MS)

    /** בדיקה + תיקון עצמי. רץ ברקע (לא ב-main thread). */
    fun run(context: Context, why: String) {
        val app = context.applicationContext
        if (GuardPolicy.credentials(app) == null || AdminState.isPaused(app)) {
            clearAlert(app)
            return
        }
        AccessibilityGuard.ensureEnabled(app, "watchdog $why")
        val p = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val now = System.currentTimeMillis()
        if (DeviceOwnerManager.isDeviceOwner(app) && now - p.getLong(K_DPC_AT, 0L) > DPC_EVERY_MS) {
            p.edit().putLong(K_DPC_AT, now).apply()
            runCatching { DeviceOwnerManager.apply(app, GuardPolicy.get(app)) }
                .onFailure { Log.w(TAG, "watchdog: device owner re-apply failed", it) }
        }
        val power = app.getSystemService(PowerManager::class.java)
        val gone = missing(
            vpnPermission = !TunnelManager.isInitialized || TunnelManager.isPermissionGranted(),
            guardNeeded = guardRequired(app),
            guardOn = GuardPolicy.isServiceEnabled(app),
            batteryOk = power?.isIgnoringBatteryOptimizations(app.packageName),
        )
        if (gone.isEmpty()) {
            if (!p.getBoolean(K_WAS_OK, false)) p.edit().putBoolean(K_WAS_OK, true).apply()
            clearAlert(app)
            return
        }
        // בהקמה הראשונה האפליקציה עצמה מובילה צעד אחר צעד — ההתראה היא למה שנפל אחרי שהכול עבד
        if (!p.getBoolean(K_WAS_OK, false)) return
        if (!shouldAlert(gone, p.getString(K_ALERTED, "").orEmpty(), p.getLong(K_ALERTED_AT, 0L), now)) return
        p.edit().putString(K_ALERTED, gone.joinToString(",")).putLong(K_ALERTED_AT, now).apply()
        Log.w(TAG, "watchdog ($why): missing ${gone.joinToString()}")
        alert(app, gone)
    }

    private fun alert(context: Context, gone: List<String>) {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CHANNEL_ID) == null) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL_ID, context.getString(R.string.alerts_channel),
                NotificationManager.IMPORTANCE_HIGH))
        }
        val what = gone.joinToString(" · ") {
            context.getString(when (it) {
                VPN -> R.string.watchdog_vpn
                GUARD -> R.string.watchdog_guard
                else -> R.string.watchdog_battery
            })
        }
        val open = context.packageManager.getLaunchIntentForPackage(context.packageName) ?: return
        val pi = PendingIntent.getActivity(context, NOTIFICATION_ID, open,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        runCatching {
            nm.notify(NOTIFICATION_ID, Notification.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_shield)
                .setContentTitle(context.getString(R.string.watchdog_title))
                .setContentText(what)
                .setStyle(Notification.BigTextStyle().bigText(
                    "$what\n${context.getString(R.string.watchdog_tap)}"))
                .setCategory(Notification.CATEGORY_ERROR)
                .setOngoing(true)                       // לא נמחקת בהחלקה — יורדת כשהכול תקין
                .setContentIntent(pi)
                .build())
        }
    }

    private fun clearAlert(context: Context) {
        val p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (p.getString(K_ALERTED, "").isNullOrEmpty()) return
        p.edit().remove(K_ALERTED).remove(K_ALERTED_AT).apply()
        runCatching { context.getSystemService(NotificationManager::class.java)?.cancel(NOTIFICATION_ID) }
    }
}
