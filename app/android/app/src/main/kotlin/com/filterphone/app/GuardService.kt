package com.filterphone.app

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.content.pm.ApplicationInfo
import android.graphics.Rect
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.Toast
import java.util.ArrayDeque

/**
 * מגן FilterPhone — שירות נגישות שאוכף בטלפון עצמו את מה שהמנהל חסם:
 *   - אפליקציה חסומה נפתחה → חזרה למסך הבית.
 *   - מסך חסום נפתח (למשל צפייה בסטטוס בוואטסאפ) → "אחורה".
 *   - תמונת פרופיל בוואטסאפ נפתחה (אם המנהל חסם) → "אחורה" (ראו WaProfileDetector).
 *
 * פרטיות: השירות רואה איזו אפליקציה / איזה מסך נפתחו. רק בוואטסאפ, ורק כשהמנהל חסם תמונות
 * פרופיל, הוא בודק גם מזהים ומיקום של רכיבי תמונה — לא קורא טקסט, הודעות או הקלדות, ולא שומר כלום.
 *
 * עמידות: עובד בלי רשת (המדיניות השמורה), אחרי אתחול / מוות של התהליך (המערכת מחברת את
 * השירות מחדש → onServiceConnected), ובודק את המסך הנוכחי מיד כשהמדיניות משתנה.
 */
class GuardService : AccessibilityService() {
    private val main = Handler(Looper.getMainLooper())
    private var lastBlockAt = 0L
    private var lastBlockPkg = ""
    private var lastToastAt = 0L
    private var foreground: String? = null
    private var contentEvents = false
    private var lastScanAt = 0L
    private var waStrikes = 0
    private var waFirstStrikeAt = 0L
    private var lastAliveMark = 0L
    private var lastWaWindow: String? = null        // המסך האחרון של וואטסאפ (Activity) — לשכבת ההחרגה
    private val debug by lazy { (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0 }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        markAlive(force = true)
        reconfigure()
        recheckNow()
        Recovery.syncSoon(this)            // לדווח לשרת שהמגן חזר
    }

    /** סוגי האירועים: תמיד מעבר בין מסכים; שינויי תוכן — רק בוואטסאפ, רק כשצריך זיהוי תמונות פרופיל. */
    private fun reconfigure() {
        val wa = GuardPolicy.get(this).waProfile
        val wantContent = wa != null && wa.packages.contains(foreground)
        if (serviceInfo == null) return
        val info = serviceInfo
        info.eventTypes = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED or
            (if (wantContent) AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED or AccessibilityEvent.TYPE_VIEW_SCROLLED else 0)
        info.flags = info.flags or AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
        info.notificationTimeout = if (wantContent) 150 else 0
        runCatching { serviceInfo = info }
        contentEvents = wantContent
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        val pkg = event.packageName?.toString() ?: return
        val policy = GuardPolicy.get(this)
        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            markAlive()
            trackForeground(pkg, policy)
            if (!policy.isEmpty) onWindow(pkg, event.className?.toString(), policy)
        } else if (contentEvents) {
            onContent(pkg, policy)
        }
    }

    private fun trackForeground(pkg: String, policy: GuardPolicy.Policy) {
        // מקלדת / שורת המערכת / FilterPhone עצמה לא "מחליפות" את האפליקציה שבחזית
        if (pkg == packageName || pkg == "com.android.systemui") return
        if (pkg in GuardPolicy.untouchable(this) && !isLauncher(pkg)) return
        if (pkg != foreground) {
            foreground = pkg
            val wa = policy.waProfile
            if ((wa != null && wa.packages.contains(pkg)) != contentEvents) reconfigure()
        }
    }

    private fun isLauncher(pkg: String): Boolean =
        runCatching {
            val home = android.content.Intent(android.content.Intent.ACTION_MAIN).addCategory(android.content.Intent.CATEGORY_HOME)
            packageManager.queryIntentActivities(home, 0).any { it.activityInfo.packageName == pkg }
        }.getOrDefault(false)

    private fun onWindow(pkg: String, cls: String?, policy: GuardPolicy.Policy) {
        when (GuardPolicy.check(this, policy, pkg, cls)) {
            GuardPolicy.Block.APP -> {
                block(pkg, GLOBAL_ACTION_HOME, R.string.guard_app_blocked)
                ProtectionState.inc(this, ProtectionState.APPS_BLOCKED)
                return
            }
            GuardPolicy.Block.SCREEN -> {
                block(pkg, GLOBAL_ACTION_BACK, R.string.guard_screen_blocked)
                ProtectionState.inc(this, ProtectionState.SCREENS_BLOCKED)
                return
            }
            null -> Unit
        }
        val wa = policy.waProfile ?: return
        val detector = WaProfileDetector(wa)
        if (!detector.watches(pkg)) return
        if (cls != null) lastWaWindow = cls
        if (detector.excluded(lastWaWindow)) return
        val rule = detector.matchWindow(pkg, cls) ?: scan(detector)
        if (rule != null) blockProfilePhoto(pkg, rule, cls)
    }

    private fun onContent(pkg: String, policy: GuardPolicy.Policy) {
        val wa = policy.waProfile ?: return
        val detector = WaProfileDetector(wa)
        if (!detector.watches(pkg) || detector.excluded(lastWaWindow)) return
        val now = SystemClock.elapsedRealtime()
        if (now - lastScanAt < SCAN_EVERY_MS) return       // עד ~4 סריקות בשנייה, גם בגלילה מהירה
        lastScanAt = now
        scan(detector)?.let { blockProfilePhoto(pkg, it, null) }
    }

    /** שכבות 2–3: מעבר מוגבל על עץ הרכיבים של החלון הפעיל (עד MAX_NODES), בלי לקרוא טקסט. */
    private fun scan(detector: WaProfileDetector): String? {
        val root = runCatching { rootInActiveWindow }.getOrNull() ?: return null
        if (!detector.watches(root.packageName?.toString())) {
            recycle(root)
            return null
        }
        val dm = resources.displayMetrics
        val screen = (dm.widthPixels.toLong() * dm.heightPixels).coerceAtLeast(1)
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        val seen = ArrayList<AccessibilityNodeInfo>()
        queue.add(root)
        val rect = Rect()
        val nodes = sequence {
            var visited = 0
            while (queue.isNotEmpty() && visited < MAX_NODES) {
                val n = queue.poll() ?: break
                seen += n
                visited++
                if (!n.isVisibleToUser) continue
                n.getBoundsInScreen(rect)
                val area = (rect.width().toLong().coerceAtLeast(0) * rect.height().toLong().coerceAtLeast(0)).toFloat() / screen
                val cls = n.className?.toString()
                val isImage = cls != null && (cls.endsWith("ImageView") || cls.endsWith("ImageButton"))
                yield(WaProfileDetector.Node(
                    viewId = n.viewIdResourceName,
                    className = cls,
                    description = if (isImage) n.contentDescription?.toString() else null,   // רק ברכיבי תמונה
                    areaFraction = area,
                ))
                for (i in 0 until n.childCount) n.getChild(i)?.let { queue.add(it) }
            }
        }
        return try {
            detector.matchNodes(nodes)
        } catch (e: Exception) {
            null
        } finally {
            seen.forEach { recycle(it) }
            queue.forEach { recycle(it) }
        }
    }

    @Suppress("DEPRECATION")
    private fun recycle(n: AccessibilityNodeInfo) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) runCatching { n.recycle() }
    }

    /**
     * יציאה מצפייה בתמונת פרופיל: "אחורה". אם אחרי כמה ניסיונות המסך עדיין שם (וואטסאפ "החזיר"
     * אותו) — מסך הבית. בלי לולאה: כל פעולה פעם אחת בחלון זמן, ואחר כך הפוגה.
     */
    private fun blockProfilePhoto(pkg: String, rule: String, cls: String?) {
        val now = SystemClock.elapsedRealtime()
        if (now - waFirstStrikeAt > 5_000) {
            waStrikes = 0
            waFirstStrikeAt = now
        }
        waStrikes++
        if (debug) Log.d(TAG, "wa-profile: rule=$rule window=${cls ?: "-"} strike=$waStrikes")
        val action = if (waStrikes >= 3) GLOBAL_ACTION_HOME else GLOBAL_ACTION_BACK
        if (block(pkg, action, R.string.guard_profile_photo_blocked, minGapMs = 700)) {
            ProtectionState.inc(this, ProtectionState.WA_PHOTOS_BLOCKED)
        }
    }

    private fun block(pkg: String, action: Int, message: Int, minGapMs: Long = 400): Boolean {
        val now = SystemClock.elapsedRealtime()
        // אפליקציה שנסגרת שולחת כמה אירועים ברצף — פעולה אחת מספיקה
        if (pkg == lastBlockPkg && now - lastBlockAt < minGapMs) return false
        lastBlockPkg = pkg
        lastBlockAt = now
        performGlobalAction(action)
        if (now - lastToastAt > 3_000) {
            lastToastAt = now
            Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
        }
        return true
    }

    /** המדיניות השתנתה (או השירות רק התחבר): בודקים את מה שפתוח עכשיו, בלי לחכות למעבר מסך. */
    private fun recheckNow() {
        val root = runCatching { rootInActiveWindow }.getOrNull() ?: return
        val pkg = root.packageName?.toString()
        recycle(root)
        if (pkg == null) return
        val policy = GuardPolicy.get(this)
        trackForeground(pkg, policy)
        if (!policy.isEmpty) onWindow(pkg, null, policy)
    }

    private fun markAlive(force: Boolean = false) {
        val now = SystemClock.elapsedRealtime()
        if (!force && now - lastAliveMark < 60_000) return
        lastAliveMark = now
        ProtectionState.mark(this, ProtectionState.GUARD_ALIVE)
    }

    override fun onInterrupt() = Unit

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        instance = null
        Recovery.syncSoon(this)            // המגן כובה — לדווח לשרת
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        instance = null
        main.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    companion object {
        private const val TAG = "FilterPhone"
        private const val SCAN_EVERY_MS = 250L
        private const val MAX_NODES = 300

        @Volatile private var instance: GuardService? = null

        val isRunning: Boolean get() = instance != null

        /** נקרא אחרי שמירת מדיניות חדשה (מכל חוט). */
        fun onPolicyChanged() {
            val s = instance ?: return
            s.main.post {
                s.reconfigure()
                s.recheckNow()
            }
        }
    }
}
