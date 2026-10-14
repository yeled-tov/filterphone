package com.filterphone.app

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.database.ContentObserver
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log

/**
 * שומר על מגן FilterPhone (שירות הנגישות) דלוק.
 *
 * עם ההרשאה WRITE_SECURE_SETTINGS — שניתנת רק בהקמה (root, או מחשב:
 *   adb shell pm grant com.filterphone.app android.permission.WRITE_SECURE_SETTINGS)
 * אם מכבים את המגן בהגדרות הנגישות, הוא חוזר מיד: מאזינים לשינוי ברשימת השירותים הפעילים, וגם
 * בכל עלייה של האפליקציה (אתחול, סנכרון, השירות הקדמי) ובניתוק של השירות עצמו (onUnbind).
 * במנהל מכשיר (אנדרואיד 11+) גם "עצירה בכוח" של FilterPhone חסומה — ראו DeviceOwnerManager.
 *
 * לא מחזירים: בהשהיה שהמנהל אישר (הטלפון פתוח), או כשהמכשיר לא רשום. בלי ההרשאה — רק דיווח למנהל.
 * שירותי נגישות אחרים ברשימה לא משתנים.
 */
object AccessibilityGuard {
    private const val TAG = "FilterPhone"
    private const val HEAL_DELAY_MS = 400L
    @Volatile private var observer: ContentObserver? = null

    fun canHeal(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.WRITE_SECURE_SETTINGS) == PackageManager.PERMISSION_GRANTED

    /** צריך להיות דלוק: המכשיר רשום ואין השהיה מאושרת. */
    fun wanted(context: Context): Boolean =
        !AdminState.isPaused(context) && GuardPolicy.credentials(context) != null

    /** מדליק את המגן אם כבוי (רק עם ההרשאה). מחזיר true אם בסוף הוא מופעל בהגדרות. */
    fun ensureEnabled(context: Context, reason: String): Boolean {
        if (GuardPolicy.isServiceEnabled(context)) return true
        if (!canHeal(context) || !wanted(context)) return false
        val cr = context.contentResolver
        val me = ComponentName(context, GuardService::class.java).flattenToString()
        return try {
            val current = Settings.Secure.getString(cr, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES).orEmpty()
            Settings.Secure.putString(cr, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES, merge(current, me))
            Settings.Secure.putInt(cr, Settings.Secure.ACCESSIBILITY_ENABLED, 1)
            ProtectionState.inc(context, ProtectionState.GUARD_RESTORED)
            Log.i(TAG, "accessibility guard re-enabled ($reason)")
            true
        } catch (e: SecurityException) {
            Log.w(TAG, "accessibility guard: no permission to re-enable")
            false
        }
    }

    /** מאזין לשינויים ברשימת שירותי הנגישות (פעם אחת לתהליך). */
    fun watch(context: Context) {
        if (observer != null || !canHeal(context)) return
        val app = context.applicationContext
        val handler = Handler(Looper.getMainLooper())
        val heal = Runnable { Recovery.runAsync(app) { ensureEnabled(app, "settings changed") } }
        val o = object : ContentObserver(handler) {
            override fun onChange(selfChange: Boolean) {
                handler.removeCallbacks(heal)
                handler.postDelayed(heal, HEAL_DELAY_MS)
            }
        }
        runCatching {
            app.contentResolver.registerContentObserver(
                Settings.Secure.getUriFor(Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES), false, o)
            app.contentResolver.registerContentObserver(
                Settings.Secure.getUriFor(Settings.Secure.ACCESSIBILITY_ENABLED), false, o)
            observer = o
        }.onFailure { Log.w(TAG, "accessibility watch not registered", it) }
    }

    /** מוסיף את השירות לרשימה המופרדת ב-':' — בלי כפילות ובלי לגעת בשאר. טהור — נבדק ביחידה. */
    fun merge(current: String, me: String): String {
        val parts = current.split(':').map { it.trim() }.filter { it.isNotEmpty() }
        if (parts.any { normalize(it).equals(normalize(me), ignoreCase = true) }) return parts.joinToString(":")
        return (parts + me).joinToString(":")
    }

    /** "pkg/.Cls" → "pkg/pkg.Cls" (אנדרואיד שומר את שתי הצורות). */
    private fun normalize(component: String): String {
        val pkg = component.substringBefore('/')
        val cls = component.substringAfter('/', "")
        return if (cls.startsWith(".")) "$pkg/$pkg$cls" else component
    }
}
