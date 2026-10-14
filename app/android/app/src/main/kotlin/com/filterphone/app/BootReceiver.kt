package com.filterphone.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * אחרי אתחול או עדכון של האפליקציה: מחזירים את ההגנה.
 *   - השירות הקדמי (מותר להפעיל מ-BOOT_COMPLETED) — מחבר את ה-VPN ומגיב לשינויי רשת.
 *   - המשימה התקופתית (JobScheduler) — גיבוי: ניסיונות חוזרים עם השהיה, ודיווח לשרת.
 * מיד אחרי אתחול לרוב אין עדיין רשת / DNS — לכן לא "ניסיון אחד ונגמר", אלא ניסיונות חוזרים
 * (Backoff) וחיבור לכתובת ה-IP של השרת כשהשם לא מתפענח.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        TunnelManager.init(context)
        Recovery.scheduleDaily(context)
        if (intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            // עדכון עצמי במנהל מכשיר: מחזירים מיד חסימות ששוחררו לזמן ההתקנה
            Recovery.runAsync(context) { SelfUpdate.restoreRestrictions(context) }
        }
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            Recovery.runAsync(context) { ManagedApps.restoreRestrictions(context) }
        }
        if (!TunnelManager.wantsConnection() || !TunnelManager.hasConfig()) return
        if (!Recovery.startProtection(context)) {
            // השירות הקדמי לא הותר — מחברים ברקע, ואם נכשל JobScheduler ינסה שוב
            Recovery.runAsync(context) { TunnelManager.ensureConnected("boot") }
        }
        Recovery.syncSoon(context)
    }
}
