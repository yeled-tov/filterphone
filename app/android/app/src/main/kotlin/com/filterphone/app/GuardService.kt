package com.filterphone.app

import android.accessibilityservice.AccessibilityService
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.accessibility.AccessibilityEvent
import android.widget.Toast
import java.util.concurrent.Executors

/**
 * מגן FilterPhone — שירות נגישות שאוכף את מה שהמנהל חסם בטלפון עצמו:
 *   - אפליקציה חסומה נפתחה → חזרה למסך הבית.
 *   - מסך חסום נפתח (למשל צפייה בסטטוס בוואטסאפ) → "אחורה".
 * השירות מקבל רק את שם האפליקציה והמסך שנפתחו — הוא לא קורא תוכן, הודעות או הקלדות
 * (canRetrieveWindowContent=false בהגדרות השירות).
 */
class GuardService : AccessibilityService() {
    private val main = Handler(Looper.getMainLooper())
    private val io = Executors.newSingleThreadExecutor()
    private var lastBlockAt = 0L
    private var lastBlockPkg = ""
    private var lastToastAt = 0L

    private val refresher = object : Runnable {
        override fun run() {
            io.execute { GuardPolicy.refreshFromServer(applicationContext) }
            main.postDelayed(this, REFRESH_MS)
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        main.removeCallbacks(refresher)
        main.postDelayed(refresher, FIRST_REFRESH_MS)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null || event.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return
        val pkg = event.packageName?.toString() ?: return
        val policy = GuardPolicy.get(this)
        if (policy.isEmpty) return
        when (GuardPolicy.check(this, policy, pkg, event.className?.toString())) {
            GuardPolicy.Block.APP -> block(pkg, GLOBAL_ACTION_HOME, R.string.guard_app_blocked)
            GuardPolicy.Block.SCREEN -> block(pkg, GLOBAL_ACTION_BACK, R.string.guard_screen_blocked)
            null -> Unit
        }
    }

    private fun block(pkg: String, action: Int, message: Int) {
        val now = SystemClock.elapsedRealtime()
        // אפליקציה שנסגרת שולחת כמה אירועים ברצף — פעולה אחת מספיקה
        if (pkg == lastBlockPkg && now - lastBlockAt < 400) return
        lastBlockPkg = pkg
        lastBlockAt = now
        performGlobalAction(action)
        if (now - lastToastAt > 3_000) {
            lastToastAt = now
            Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
        }
    }

    override fun onInterrupt() = Unit

    override fun onDestroy() {
        main.removeCallbacks(refresher)
        io.shutdownNow()
        super.onDestroy()
    }

    private companion object {
        const val FIRST_REFRESH_MS = 20_000L
        const val REFRESH_MS = 30 * 60_000L
    }
}
