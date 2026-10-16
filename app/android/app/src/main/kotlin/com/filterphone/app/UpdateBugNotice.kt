package com.filterphone.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context

/**
 * רשת ביטחון לבאג של אנדרואיד 16 (ראו TunnelManager.pauseForUpdate): אם בכל זאת עודכנו כשה-VPN
 * פעיל (למשל התקנה ידנית מהדפדפן) והמנהרה למעלה בלי תעבורה — שום דבר באפליקציה לא מתקן את זה,
 * רק הפעלה מחדש של הטלפון. התראה אחת לכל עדכון, ורק בשעות הראשונות אחריו.
 */
object UpdateBugNotice {
    private const val CHANNEL_ID = "alerts"
    private const val NOTIFICATION_ID = 7301
    private const val PREFS = "fp_update_bug"
    private const val K_NOTIFIED_FOR = "notified_for"       // lastUpdateTime של העדכון שכבר התרענו עליו
    private const val WINDOW_MS = 6 * 3600_000L

    /** טהור: אנדרואיד 16, עדכון בשעות האחרונות, ועוד לא התרענו עליו. */
    fun shouldShow(sdk: Int, lastUpdate: Long, notifiedFor: Long, now: Long): Boolean =
        sdk == 36 && lastUpdate > 0 && now - lastUpdate in 0..WINDOW_MS && notifiedFor != lastUpdate

    fun maybeShow(context: Context) {
        val app = context.applicationContext
        val lastUpdate = runCatching { app.packageManager.getPackageInfo(app.packageName, 0).lastUpdateTime }
            .getOrDefault(0L)
        val p = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (!shouldShow(android.os.Build.VERSION.SDK_INT, lastUpdate, p.getLong(K_NOTIFIED_FOR, 0L),
                System.currentTimeMillis())) return
        p.edit().putLong(K_NOTIFIED_FOR, lastUpdate).apply()
        val nm = app.getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CHANNEL_ID) == null) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL_ID, app.getString(R.string.alerts_channel),
                NotificationManager.IMPORTANCE_HIGH))
        }
        runCatching {
            nm.notify(NOTIFICATION_ID, Notification.Builder(app, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_shield)
                .setContentTitle(app.getString(R.string.update_bug_title))
                .setContentText(app.getString(R.string.update_bug_text))
                .setStyle(Notification.BigTextStyle().bigText(app.getString(R.string.update_bug_text)))
                .setCategory(Notification.CATEGORY_ERROR)
                .build())
        }
    }
}
