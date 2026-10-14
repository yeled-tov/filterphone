package com.filterphone.app

import android.app.job.JobInfo
import android.app.job.JobScheduler
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.util.Log
import java.util.concurrent.Executors

/**
 * התאוששות ברקע — בלי לולאות:
 *   - runAsync: עבודה ברקע (חיבור VPN חוסם עד 25 שניות — אסור ב-main thread).
 *   - scheduleRetry: ניסיון חיבור נוסף דרך JobScheduler אחרי ההשהיה (ה-Backoff קובע אותה).
 *     JobScheduler שורד מוות של התהליך ומכבד את מצב Doze — המערכת מריצה כשמתאים לה.
 *   - schedulePeriodic: בדיקה + דיווח לשרת כל 15 דקות (המינימום של אנדרואיד), גם אחרי אתחול.
 *   - startProtection: השירות הקדמי (התראה קבועה) שמגיב מיד לשינויי רשת.
 */
object Recovery {
    private const val TAG = "FilterPhone"
    const val JOB_PERIODIC = 4201
    const val JOB_NOW = 4202
    const val JOB_RETRY = 4203
    const val JOB_RESUME = 4204          // סוף השהיה שהמנהל אישר — מחזירים את כל ההגנה בזמן
    private const val PERIOD_MS = 15 * 60_000L

    private val executor = Executors.newSingleThreadExecutor { r -> Thread(r, "fp-recovery").apply { isDaemon = true } }

    fun runAsync(context: Context, block: () -> Unit) {
        val app = context.applicationContext
        executor.execute {
            try {
                TunnelManager.init(app)
                block()
            } catch (e: Exception) {
                Log.w(TAG, "background task failed", e)
            }
        }
    }

    private fun scheduler(context: Context) = context.getSystemService(JobScheduler::class.java)
    private fun component(context: Context) = ComponentName(context, SyncJob::class.java)

    /** תקופתי: בדיקת VPN + דיווח לשרת + רענון מדיניות. נשמר גם אחרי אתחול (persisted). */
    fun schedulePeriodic(context: Context) {
        val js = scheduler(context) ?: return
        if (js.getPendingJob(JOB_PERIODIC) != null) return
        val job = JobInfo.Builder(JOB_PERIODIC, component(context))
            .setPeriodic(PERIOD_MS)
            .setPersisted(true)
            .build()
        runCatching { js.schedule(job) }.onFailure { Log.w(TAG, "periodic job not scheduled", it) }
    }

    /** "עכשיו" (אחרי שינוי מדיניות / פתיחת האפליקציה / אתחול): בהקדם, כשיש רשת. */
    fun syncSoon(context: Context) {
        val js = scheduler(context) ?: return
        val job = JobInfo.Builder(JOB_NOW, component(context))
            .setMinimumLatency(1_000)
            .setOverrideDeadline(60_000)
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .build()
        runCatching { js.schedule(job) }
    }

    /** ניסיון חיבור VPN נוסף אחרי delayMs. בלי דרישת רשת: ב"חסימה בלי VPN" אנדרואיד מדווח "אין רשת". */
    fun scheduleRetry(context: Context, delayMs: Long) {
        val js = scheduler(context) ?: return
        val pending = js.getPendingJob(JOB_RETRY)
        if (pending != null && pending.minLatencyMillis <= delayMs) return      // ניסיון קרוב יותר כבר מתוזמן
        val job = JobInfo.Builder(JOB_RETRY, component(context))
            .setMinimumLatency(delayMs.coerceAtLeast(1_000))
            .setOverrideDeadline(delayMs.coerceAtLeast(1_000) + 5 * 60_000L)
            .build()
        runCatching { js.schedule(job) }
    }

    fun cancelAll(context: Context) {
        val js = scheduler(context) ?: return
        listOf(JOB_PERIODIC, JOB_NOW, JOB_RETRY, JOB_RESUME).forEach { js.cancel(it) }
    }

    /** בסוף ההשהיה (גם אם האפליקציה סגורה / הטלפון אותחל): VPN, מגן, מנהל מכשיר — הכול חוזר. */
    fun scheduleResume(context: Context, delayMs: Long) {
        val js = scheduler(context) ?: return
        val job = JobInfo.Builder(JOB_RESUME, component(context))
            .setMinimumLatency(delayMs.coerceAtLeast(1_000))
            .setOverrideDeadline(delayMs.coerceAtLeast(1_000) + 60_000)
            .setPersisted(true)
            .build()
        runCatching { js.schedule(job) }.onFailure { Log.w(TAG, "resume job not scheduled", it) }
    }

    /**
     * ההשהיה התחילה / נגמרה (פקודת מנהל, פקיעת תוקף או "החזר הגנה" בטלפון). רץ ברקע.
     * בהשהיה: המנהרה יורדת, המגן לא חוסם, מנהל המכשיר מרפה הכול (DeviceOwnerManager.PAUSED).
     * בסיום: הכול מוחל מחדש ומתחבר מיד.
     */
    fun onPauseChanged(context: Context) {
        val app = context.applicationContext
        val paused = AdminState.isPaused(app)
        runCatching { DeviceOwnerManager.apply(app, GuardPolicy.get(app)) }
            .onFailure { Log.w(TAG, "device owner apply on pause change failed", it) }
        GuardService.onPolicyChanged()
        if (paused) {
            TunnelManager.healthCheck()                          // מוריד את המנהרה (pausedGate)
            scheduleResume(app, AdminState.pauseUntil(app) - System.currentTimeMillis() + 1_000)
        } else {
            scheduler(app)?.cancel(JOB_RESUME)
            TunnelManager.ensureConnected("resume")
            startProtection(app)
        }
        ProtectionService.refresh(app)
    }

    /**
     * השירות הקדמי. מותר להפעיל מהאפליקציה, מ-BOOT_COMPLETED, ממנהל מכשיר, או כשהאפליקציה פטורה
     * מאופטימיזציית סוללה. אחרת אנדרואיד (12+) מסרב — ואז ההתאוששות נשענת על JobScheduler בלבד.
     */
    fun startProtection(context: Context): Boolean {
        if (!TunnelManager.isInitialized) TunnelManager.init(context)
        if (!TunnelManager.wantsConnection()) return false
        return try {
            context.startForegroundService(Intent(context, ProtectionService::class.java))
            true
        } catch (e: Exception) {
            // אנדרואיד 12+: ForegroundServiceStartNotAllowedException כשמפעילים מהרקע בלי פטור
            Log.w(TAG, "protection service not started: ${e.javaClass.simpleName}")
            false
        }
    }
}
