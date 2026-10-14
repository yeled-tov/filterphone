package com.filterphone.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.util.Log

/**
 * התקנה / הסרה / עדכון של אפליקציה — בזמן אמת (רישום דינמי; מאנדרואיד 8 אי אפשר דרך המניפסט):
 *   - אפליקציה מרשימת "מחוץ ל-VPN" (FilterTube) — בונים את המנהרה מחדש, כדי שתעבוד מיד.
 *   - אפליקציה חסומה הותקנה במנהל מכשיר — מחילים שוב (אפור / מוסתרת) לפני שמספיקים להשתמש בה.
 * רשת הביטחון: בדיקת התקינות התקופתית של המנהרה והסנכרון כל 15 דקות עושים את אותו הדבר.
 */
object PackageWatch {
    private const val TAG = "FilterPhone"
    @Volatile private var receiver: BroadcastReceiver? = null

    fun register(context: Context) {
        if (receiver != null) return
        val r = object : BroadcastReceiver() {
            override fun onReceive(ctx: Context, intent: Intent) {
                val pkg = intent.data?.schemeSpecificPart ?: return
                if (pkg == ctx.packageName) return
                val app = ctx.applicationContext
                Recovery.runAsync(app) { onChanged(app, pkg, intent.action ?: "") }
            }
        }
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_PACKAGE_ADDED)
            addAction(Intent.ACTION_PACKAGE_REMOVED)
            addAction(Intent.ACTION_PACKAGE_REPLACED)
            addDataScheme("package")
        }
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                context.applicationContext.registerReceiver(r, filter, Context.RECEIVER_NOT_EXPORTED)
            } else {
                context.applicationContext.registerReceiver(r, filter)
            }
            receiver = r
        } catch (e: Exception) {
            Log.w(TAG, "package watch not registered: ${e.javaClass.simpleName}")
        }
    }

    fun unregister(context: Context) {
        val r = receiver ?: return
        receiver = null
        runCatching { context.applicationContext.unregisterReceiver(r) }
    }

    private fun onChanged(context: Context, pkg: String, action: String) {
        if (VpnBypass.listed(context, pkg)) {
            Log.i(TAG, "bypass app changed ($action) — refreshing the tunnel")
            TunnelManager.refreshBypass("package")
        }
        if (action != Intent.ACTION_PACKAGE_REMOVED && pkg in GuardPolicy.get(context).apps &&
            DeviceOwnerManager.isDeviceOwner(context)) {
            runCatching { DeviceOwnerManager.apply(context, GuardPolicy.get(context)) }
                .onFailure { Log.w(TAG, "re-apply after install failed", it) }
        }
    }
}
