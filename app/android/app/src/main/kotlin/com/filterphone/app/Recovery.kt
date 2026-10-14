package com.filterphone.app

import android.app.job.JobInfo
import android.app.job.JobScheduler
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.util.Log
import java.util.Calendar
import java.util.concurrent.Executors
import kotlin.random.Random

/**
 * התאוששות ברקע — בלי לולאות ובלי לרוקן סוללה:
 *   - runAsync: עבודה ברקע (חיבור VPN חוסם עד 25 שניות — אסור ב-main thread).
 *   - scheduleRetry: ניסיון חיבור נוסף דרך JobScheduler אחרי ההשהיה (ה-Backoff קובע אותה).
 *     JobScheduler שורד מוות של התהליך ומכבד את מצב Doze — המערכת מריצה כשמתאים לה.
 *   - scheduleDaily: תחזוקה אחת ביממה (~02:00) — בדיקת גרסה + התקנה אוטומטית + סנכרון מדיניות.
 *     אין דיווח/בדיקה תקופתיים כל כמה דקות (זה מה שרוקן סוללה). שינויי מדיניות מהדשבורד מוחלים
 *     כשהאפליקציה נפתחת או ברענון ידני (syncSoon), והחיבור מתוחזק מונחה-אירועים (רשת + הדלקת מסך).
 *   - startProtection: השירות הקדמי (התראה קבועה) שמגיב מיד לשינויי רשת.
 */
object Recovery {
    private const val TAG = "FilterPhone"
    const val JOB_PERIODIC = 4201        // ישן — נשאר לביטול בלבד (גרסאות קודמות תזמנו אותו כל 15 דק')
    const val JOB_NOW = 4202
    const val JOB_RETRY = 4203
    const val JOB_RESUME = 4204          // סוף השהיה שהמנהל אישר — מחזירים את כל ההגנה בזמן
    const val JOB_DAILY = 4205           // תחזוקה יומית: בדיקת גרסה + התקנה + סנכרון
    const val DAILY_HOUR = 2             // ~02:00 מקומי
    private const val DAILY_WINDOW_MS = 3 * 60 * 60_000L   // חלון ביצוע: עד 05:00 (כשיש רשת/טעינה)

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

    /**
     * תחזוקה יומית (~02:00): בדיקת גרסה חדשה + התקנה אוטומטית (במנהל מכשיר), סנכרון מדיניות ודיווח.
     * מתוזמן כעבודה חד-פעמית עם חלון (JobScheduler לא יודע שעון-קיר), ומתזמן מחדש את היום הבא כשרץ.
     * נשמר אחרי אתחול. מבטל את המשרה הישנה (כל 15 דק') אם נשארה מגרסה קודמת.
     */
    fun scheduleDaily(context: Context) {
        val js = scheduler(context) ?: return
        runCatching { js.cancel(JOB_PERIODIC) }                       // ישן — כל 15 דקות
        if (js.getPendingJob(JOB_DAILY) != null) return
        val delay = delayToHourMs(System.currentTimeMillis(), DAILY_HOUR, Random.nextLong(30 * 60_000L))
        val job = JobInfo.Builder(JOB_DAILY, component(context))
            .setMinimumLatency(delay)
            .setOverrideDeadline(delay + DAILY_WINDOW_MS)
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setPersisted(true)
            .build()
        runCatching { js.schedule(job) }.onFailure { Log.w(TAG, "daily job not scheduled", it) }
    }

    /** מתזמן מחדש את התחזוקה של מחר (נקרא בסוף ריצת JOB_DAILY). */
    fun rescheduleDaily(context: Context) {
        scheduler(context)?.cancel(JOB_DAILY)
        scheduleDaily(context)
    }

    /**
     * כמה מילי-שניות עד השעה [hour] הבאה (מקומית), בתוספת [jitter] (לפזר עומס בשרת). טהור — נבדק ביחידה.
     * [now] במילי-שניות אפוך; מחושב לפי אזור הזמן של המכשיר.
     */
    fun delayToHourMs(now: Long, hour: Int, jitter: Long = 0): Long {
        val cal = Calendar.getInstance().apply {
            timeInMillis = now
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
        }
        if (cal.timeInMillis <= now) cal.add(Calendar.DAY_OF_YEAR, 1)
        return (cal.timeInMillis - now) + jitter.coerceAtLeast(0)
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
        listOf(JOB_PERIODIC, JOB_NOW, JOB_RETRY, JOB_RESUME, JOB_DAILY).forEach { js.cancel(it) }
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
