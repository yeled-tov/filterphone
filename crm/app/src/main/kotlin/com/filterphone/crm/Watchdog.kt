package com.filterphone.crm

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.SystemClock

/** בדיקה תקופתית (כל ~15 דקות, גם במצב שינה): מחזירה את השירות, ואם אי אפשר — בודקת בשרת בעצמה. */
object Watchdog {
    private const val EVERY_MS = 15 * 60_000L

    private fun pi(c: Context): PendingIntent = PendingIntent.getBroadcast(c, 0,
        Intent(c, WatchdogReceiver::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    fun schedule(c: Context) {
        val am = c.getSystemService(AlarmManager::class.java) ?: return
        am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, SystemClock.elapsedRealtime() + EVERY_MS, pi(c))
    }

    fun cancel(c: Context) {
        c.getSystemService(AlarmManager::class.java)?.cancel(pi(c))
    }
}

class WatchdogReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext
        if (Crm.token(app) == null) return
        Watchdog.schedule(app)
        if (WatchService.start(app, now = true)) return
        // המערכת לא מרשה להפעיל את השירות מהרקע — בדיקה אחת עכשיו, בתוך זמן המקלט
        val pending = goAsync()
        Thread {
            try {
                Poller.run(app)
            } finally {
                pending.finish()
            }
        }.start()
    }
}

/** אחרי אתחול / עדכון של האפליקציה — השירות חוזר (אם מחוברים). */
class StartReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action
        if (action != Intent.ACTION_BOOT_COMPLETED && action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val app = context.applicationContext
        if (Crm.token(app) == null) return
        if (!WatchService.start(app)) Watchdog.schedule(app)
    }
}
