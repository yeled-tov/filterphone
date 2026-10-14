package com.filterphone.app

import android.app.admin.DevicePolicyManager
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * "הסרת הכול" — רק בזמן השהיה שהמנהל אישר (AdminState), ואחרי אזהרה במסך:
 *   1. מודיעים לשרת (המכשיר מושבת שם ונרשם ביומן) — לפני שהאסימון נמחק.
 *   2. תעודת הסינון — מוסרת (במנהל מכשיר; אחרת המשתמש מסיר בהגדרות — מוסבר במסך).
 *   3. ה-VPN — מנותק וההגדרות נמחקות.
 *   4. מנהל המכשיר — כל ההגבלות משוחררות, אפליקציות חסומות חוזרות, ואז clearDeviceOwnerApp.
 *   5. מנהל מכשיר רגיל (אם הופעל) — מבוטל, כדי שאפשר יהיה להסיר.
 *   6. כל הנתונים המקומיים (מדיניות, אסימון, משימות רקע) — נמחקים.
 * אחר כך הממשק פותח את חלון הסרת האפליקציה של אנדרואיד (אפליקציה לא יכולה למחוק את עצמה בשקט).
 * אין דרך חזרה: התקנה מחדש = הרשמה/שחזור לפי מספר הטלפון.
 */
object Uninstall {
    private const val TAG = "FilterPhone"

    fun everything(context: Context): Map<String, Any?> {
        check(AdminState.isPaused(context)) { "הסרה אפשרית רק בזמן השהיה שהמנהל אישר" }
        val serverNotified = runCatching { Sync.notify(context, "/v1/device/uninstalled") }.getOrDefault(false)
        val wasOwner = DeviceOwnerManager.isDeviceOwner(context)
        val caRemoved = ProtectionState.string(context, ProtectionState.CA_PEM)?.let { pem ->
            runCatching { CertHelper.uninstallAsOwner(context, pem) }.getOrDefault(false)
        } ?: false
        runCatching { TunnelManager.reset() }.onFailure { Log.w(TAG, "vpn reset failed", it) }
        val managementCleared = DeviceOwnerManager.clearManagement(context)
        runCatching {
            val dpm = context.getSystemService(DevicePolicyManager::class.java)
            if (dpm?.isAdminActive(DeviceAdmin.component(context)) == true) dpm.removeActiveAdmin(DeviceAdmin.component(context))
        }.onFailure { Log.w(TAG, "remove device admin failed", it) }
        runCatching { Recovery.cancelAll(context) }
        runCatching { context.stopService(Intent(context, ProtectionService::class.java)) }
        GuardPolicy.clear(context)
        AdminState.clear(context)
        ProtectionState.clearAll(context)
        GuardService.onPolicyChanged()
        Log.i(TAG, "protection removed by the user during an authorized pause")
        return mapOf(
            "ok" to managementCleared,
            "was_device_owner" to wasOwner,
            "ca_removed" to caRemoved,
            "server_notified" to serverNotified,
        )
    }
}
