package com.filterphone.app

import android.app.admin.DevicePolicyManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.PowerManager
import android.os.UserManager
import android.util.Log
import java.util.Calendar

/**
 * Google Play מנוהל — שער ההתקנות של מנהל המכשיר: חסימה עוד לפני שההתקנה מתחילה, לא הסרה אחריה.
 *
 * מנהל המכשיר מחזיק את DISALLOW_INSTALL_APPS פעיל כל הזמן, כך שכל התקנה — מ-Play, מהאתר של Play
 * ("התקנה במכשיר" ממחשב), או מכל מקום אחר — נכשלת מיד ("לא ניתן להתקין"). השער נפתח רק כשהלקוח נמצא
 * בעמוד של אפליקציה מאושרת ב-Play והכפתור שם הוא "התקנה" / "עדכון" (GuardService), ונסגר:
 *   - ברגע שההתקנה של אפליקציה מאושרת התחילה (נוצר לה session — אנדרואיד בודק את החסימה רק ביצירה,
 *     ולא בסיום, כך שההתקנה ממשיכה עד הסוף; נבדק בקוד של אנדרואיד: PackageInstallerService.createSessionInternal);
 *   - כשיוצאים מהעמוד (אחרי השהיה קצרה) או מ-Play;
 *   - לכל המאוחר אחרי MAX_OPEN_MS (גם אם משהו נתקע — הבדיקה התקופתית מחזירה).
 * עדכונים אוטומטיים של אפליקציות שכבר בטלפון: בלילה (02:00–05:00), בטעינה ובמסך כבוי — השער פתוח, כדי ש-Play
 * יעדכן כרגיל. גם אז התקנה חדשה שלא אושרה מוסרת מיד (PlayManaged — השכבה השנייה).
 */
object PlayGate {
    private const val TAG = "FilterPhone"
    private const val PREFS = "fp_play_gate"
    private const val K_OPEN_AT = "open_at"
    /** לכל היותר — אחר כך השער נחשב סגור (DeviceOwnerManager מחזיר את החסימה בבדיקה הבאה). */
    const val MAX_OPEN_MS = 5 * 60_000L
    /** אחרי שיוצאים מעמוד האפליקציה המאושרת — עוד רגע (הלחיצה על "התקנה" לפעמים מקדימה את הסריקה). */
    const val LEAVE_GRACE_MS = 15_000L

    /** השער פעיל (התקנות חסומות כברירת מחדל): Play מנוהל, מנהל מכשיר, לא בהשהיה. טהור. */
    fun applies(playManaged: Boolean, deviceOwner: Boolean, paused: Boolean): Boolean =
        playManaged && deviceOwner && !paused

    /** חלון העדכונים האוטומטיים: 02:00–05:00, בטעינה, מסך כבוי. טהור. */
    fun nightWindow(hour: Int, charging: Boolean, interactive: Boolean): Boolean =
        hour in 2..4 && charging && !interactive

    /** פתוח (שוחרר לאפליקציה מאושרת ועוד לא עברו MAX_OPEN_MS). טהור. */
    fun isOpen(openAt: Long, now: Long): Boolean = openAt > 0 && now - openAt in 0 until MAX_OPEN_MS

    fun isOpen(context: Context): Boolean = isOpen(prefs(context).getLong(K_OPEN_AT, 0L), System.currentTimeMillis())

    /** האם DeviceOwnerManager צריך להחזיק עכשיו את חסימת ההתקנות בגלל Play מנוהל. */
    fun wantsRestriction(context: Context, policy: GuardPolicy.Policy): Boolean =
        applies(policy.playManaged, DeviceOwnerManager.isDeviceOwner(context), AdminState.isPaused(context)) &&
            !nightOpen(context)

    private fun nightOpen(context: Context): Boolean = runCatching {
        val app = context.applicationContext
        val battery = app.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val status = battery?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val charging = status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL
        val interactive = app.getSystemService(PowerManager::class.java)?.isInteractive ?: true
        nightWindow(Calendar.getInstance().get(Calendar.HOUR_OF_DAY), charging, interactive)
    }.getOrDefault(false)

    /** עמוד של אפליקציה מאושרת עם "התקנה" / "עדכון": מנהל המכשיר מסיר את חסימת ההתקנות לרגע. */
    fun open(context: Context) {
        val app = context.applicationContext
        if (isOpen(app)) return
        val policy = GuardPolicy.get(app)
        if (!applies(policy.playManaged, DeviceOwnerManager.isDeviceOwner(app), AdminState.isPaused(app))) return
        prefs(app).edit().putLong(K_OPEN_AT, System.currentTimeMillis()).commit()
        runCatching {
            app.getSystemService(DevicePolicyManager::class.java)
                ?.clearUserRestriction(DeviceAdmin.component(app), UserManager.DISALLOW_INSTALL_APPS)
        }.onFailure { Log.w(TAG, "play gate: open failed", it) }
        Log.i(TAG, "play gate: open (approved app page)")
    }

    /** סוגרים: חסימת ההתקנות חוזרת מיד (אלא אם התקנה שקטה של FilterPhone באמצע — היא מחזירה בסופה). */
    fun close(context: Context, why: String) {
        val app = context.applicationContext
        val p = prefs(app)
        if (p.getLong(K_OPEN_AT, 0L) == 0L) return
        p.edit().remove(K_OPEN_AT).commit()
        val busy = runCatching { SelfUpdate.installWindowOpen(app) || ManagedApps.installWindowOpen(app) }.getOrDefault(false)
        if (!busy && DeviceOwnerManager.installRestrictionWanted(app)) {
            runCatching {
                app.getSystemService(DevicePolicyManager::class.java)
                    ?.addUserRestriction(DeviceAdmin.component(app), UserManager.DISALLOW_INSTALL_APPS)
            }.onFailure { Log.w(TAG, "play gate: close failed", it) }
        }
        Log.i(TAG, "play gate: closed ($why)")
    }

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
