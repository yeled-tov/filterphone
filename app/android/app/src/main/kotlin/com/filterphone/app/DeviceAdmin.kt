package com.filterphone.app

import android.app.admin.DeviceAdminReceiver
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.PersistableBundle
import android.util.Log

/**
 * רכיב המנהל של FilterPhone. משמש לשני מצבים שונים (ראו DeviceOwnerManager):
 *   - Device Owner — אחרי הקמה ב-QR / adb (adb shell dpm set-device-owner com.filterphone.app/.DeviceAdmin).
 *   - מנהל מכשיר רגיל — כשהמשתמש מפעיל מהאפליקציה; רק מקשה על הסרה, ומדווח כשמבטלים.
 * כל שינוי במצב — מדווח לשרת (Recovery.syncSoon), כדי שהממשק יראה הסרת ניהול מיד.
 */
class DeviceAdmin : DeviceAdminReceiver() {

    override fun onEnabled(context: Context, intent: Intent) {
        Log.i("FilterPhone", "device admin enabled")
        Recovery.syncSoon(context)
    }

    override fun onDisableRequested(context: Context, intent: Intent): CharSequence =
        context.getString(R.string.admin_disable_warning)

    override fun onDisabled(context: Context, intent: Intent) {
        Log.w("FilterPhone", "device admin disabled")
        Recovery.syncSoon(context)
    }

    /** הקמה ישנה (לפני אנדרואיד 10): המערכת קוראת לזה בסוף. בגרסאות חדשות — PolicyComplianceActivity. */
    override fun onProfileProvisioningComplete(context: Context, intent: Intent) {
        Provisioning.complete(context, intent.extras?.let { b ->
            @Suppress("DEPRECATION")
            b.get(DevicePolicyManager.EXTRA_PROVISIONING_ADMIN_EXTRAS_BUNDLE) as? PersistableBundle
        })
        context.packageManager.getLaunchIntentForPackage(context.packageName)
            ?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)?.let { context.startActivity(it) }
    }

    companion object {
        fun component(context: Context) = ComponentName(context, DeviceAdmin::class.java)

        fun isDeviceOwner(context: Context): Boolean = DeviceOwnerManager.isDeviceOwner(context)
    }
}
