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
 *   - Google Play מנוהל: התקנה חדשה מ-Play שלא אושרה — מוסרת מיד (PlayManaged).
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
                val replacing = intent.getBooleanExtra(Intent.EXTRA_REPLACING, false)
                Recovery.runAsync(app) { onChanged(app, pkg, intent.action ?: "", replacing) }
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

    private const val PREFS = "fp_package_watch"
    private const val K_LOG = "log"
    private const val LOG_MAX = 15

    /**
     * יומן קצר של התקנות / עדכונים / הסרות (נשלח לשרת בדיווח): מה הותקן, מי התקין (Play / אחר), והאם זו
     * אפליקציית מערכת — כדי לדעת למה אפליקציה "חוזרת לבד".
     */
    fun log(context: Context): org.json.JSONArray =
        runCatching { org.json.JSONArray(prefs(context).getString(K_LOG, "[]")) }.getOrDefault(org.json.JSONArray())

    private fun record(context: Context, pkg: String, action: String, replacing: Boolean) {
        val pm = context.packageManager
        val info = runCatching { pm.getApplicationInfo(pkg, 0) }.getOrNull()
        val installer = runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) pm.getInstallSourceInfo(pkg).installingPackageName
            else @Suppress("DEPRECATION") pm.getInstallerPackageName(pkg)
        }.getOrNull()
        val e = org.json.JSONObject()
            .put("t", System.currentTimeMillis() / 1000)
            .put("pkg", pkg)
            .put("a", action.substringAfterLast('_').lowercase() + if (replacing) "+r" else "")
            .put("by", installer ?: "")
            .put("sys", info != null && (info.flags and android.content.pm.ApplicationInfo.FLAG_SYSTEM) != 0)
            .put("gate", PlayGate.isOpen(context))
        synchronized(this) {
            val old = log(context)
            val out = org.json.JSONArray()
            val from = maxOf(0, old.length() - (LOG_MAX - 1))
            for (i in from until old.length()) out.put(old.get(i))
            out.put(e)
            prefs(context).edit().putString(K_LOG, out.toString()).apply()
        }
    }

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun onChanged(context: Context, pkg: String, action: String, replacing: Boolean) {
        runCatching { record(context, pkg, action, replacing) }
        // Google Play מנוהל: אפליקציה חדשה מ-Play שלא אושרה — מוסרת / נחסמת, וחלון הבקשה נפתח
        if (action == Intent.ACTION_PACKAGE_ADDED) {
            runCatching { PlayManaged.onPackageAdded(context, pkg, replacing) }
                .onFailure { Log.w(TAG, "play managed check failed", it) }
        }
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
