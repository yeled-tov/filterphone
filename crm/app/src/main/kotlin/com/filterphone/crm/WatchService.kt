package com.filterphone.crm

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/**
 * שירות ההתראות: רץ ברקע עם התראה קבועה ושקטה, ובודק בשרת כל דקה. ממשיך גם כשסוגרים את האפליקציה
 * מהאחרונים (שירות קדמי לא נסגר עם המסך), חוזר אחרי אתחול (StartReceiver), ואם המערכת בכל זאת עצרה
 * אותו — הבדיקה התקופתית (WatchdogReceiver) מחזירה אותו או בודקת בעצמה.
 */
class WatchService : Service() {
    private var exec: ScheduledExecutorService? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!startInForeground()) return START_NOT_STICKY
        if (Crm.token(this) == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        val running = exec
        if (running == null) {
            exec = Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "crm-poll").apply { isDaemon = true } }
                .also { it.scheduleWithFixedDelay({ tick() }, 0, Crm.POLL_SECONDS, TimeUnit.SECONDS) }
        } else if (intent?.action == ACTION_NOW) {
            running.execute { tick() }
        }
        Watchdog.schedule(this)
        return START_STICKY
    }

    private fun tick() {
        when (Poller.run(this)) {
            Poller.Result.NO_TOKEN, Poller.Result.UNAUTHORIZED -> stopSelf()
            else -> Unit
        }
    }

    private fun startInForeground(): Boolean = try {
        val n = Notifier.service(this)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(Events.ID_SERVICE, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(Events.ID_SERVICE, n)
        }
        true
    } catch (e: Exception) {
        Log.w(Crm.TAG, "startForeground failed: ${e.javaClass.simpleName}")
        stopSelf()
        false
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        // יש מכשירים שסוגרים גם את השירות כשמחליקים מהאחרונים — הבדיקה התקופתית תחזיר אותו
        Watchdog.schedule(this)
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        exec?.shutdownNow()
        exec = null
        super.onDestroy()
    }

    companion object {
        const val ACTION_NOW = "com.filterphone.crm.POLL_NOW"

        /** מפעיל (או מעיר לבדיקה מיידית). false אם המערכת לא מרשה כרגע (רקע בלי פטור). */
        fun start(context: Context, now: Boolean = false): Boolean = try {
            val i = Intent(context, WatchService::class.java)
            if (now) i.action = ACTION_NOW
            context.startForegroundService(i)
            true
        } catch (e: Exception) {
            Log.w(Crm.TAG, "watch service not started: ${e.javaClass.simpleName}")
            false
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, WatchService::class.java))
            Watchdog.cancel(context)
        }
    }
}
