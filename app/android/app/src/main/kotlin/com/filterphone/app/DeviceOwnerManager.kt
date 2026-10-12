package com.filterphone.app

import android.Manifest
import android.app.admin.DevicePolicyManager
import android.app.admin.FactoryResetProtectionPolicy
import android.content.Context
import android.os.Build
import android.os.UserManager
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/**
 * מנהל המכשיר (Device Owner / DPC) — נפרד מ"מנהל מכשיר רגיל" (Device Admin):
 *
 *   Device Admin (ישן): המשתמש מפעיל בהגדרות, ויכול לבטל בכל רגע. נותן רק "חיכוך" בהסרה
 *     והתראה כשמבטלים. לא מונע שום דבר באמת — ולכן מוצג ככזה.
 *   Device Owner: מוגדר רק בטלפון מאופס (QR / adb / זירו-טאץ'). אנדרואיד עצמו אוכף את
 *     ההגבלות כאן — בלי נגישות ובלי "טריקים". אי אפשר לבטל אותו מההגדרות; רק איפוס יצרן.
 *
 * כל פעולה כאן היא API רשמי של DevicePolicyManager. מה שנכשל (גרסת אנדרואיד / יצרן) — מדווח
 * לשרת כ"failed", לא מוסתר.
 */
object DeviceOwnerManager {
    private const val TAG = "FilterPhone"
    private const val PREFS = "fp_dpc"
    private const val K_HIDDEN = "hidden_by_owner"
    private const val K_RESULT = "last_result"

    /** הגדרות ברירת מחדל — זהות ל-DPC_DEFAULTS בשרת (server/filterphone/api/catalog.py). */
    val DEFAULTS: Map<String, Boolean> = mapOf(
        "block_uninstall" to true,
        "always_on_vpn" to true,
        "disallow_config_vpn" to true,
        "disallow_factory_reset" to true,
        "disallow_safe_boot" to true,
        "disallow_add_user" to true,
        "disallow_unknown_sources" to true,
        "disallow_private_dns" to true,
        "disallow_debugging" to false,
        "disallow_install_apps" to false,
        "disallow_modify_accounts" to false,
        "hide_blocked_apps" to true,
    )

    /** הגבלות משתמש (UserManager) לפי מפתח. */
    private val RESTRICTIONS: Map<String, List<String>> by lazy {
        buildMap {
            put("disallow_config_vpn", listOf(UserManager.DISALLOW_CONFIG_VPN))
            put("disallow_factory_reset", listOf(UserManager.DISALLOW_FACTORY_RESET))
            put("disallow_safe_boot", listOf(UserManager.DISALLOW_SAFE_BOOT))
            put("disallow_add_user", listOf(UserManager.DISALLOW_ADD_USER))
            put("disallow_unknown_sources", buildList {
                add(UserManager.DISALLOW_INSTALL_UNKNOWN_SOURCES)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) add(UserManager.DISALLOW_INSTALL_UNKNOWN_SOURCES_GLOBALLY)
            })
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                put("disallow_private_dns", listOf(UserManager.DISALLOW_CONFIG_PRIVATE_DNS))
            }
            put("disallow_debugging", listOf(UserManager.DISALLOW_DEBUGGING_FEATURES))
            put("disallow_install_apps", listOf(UserManager.DISALLOW_INSTALL_APPS))
            put("disallow_modify_accounts", listOf(UserManager.DISALLOW_MODIFY_ACCOUNTS))
        }
    }

    private fun dpm(context: Context): DevicePolicyManager? = context.getSystemService(DevicePolicyManager::class.java)
    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun isDeviceOwner(context: Context): Boolean =
        runCatching { dpm(context)?.isDeviceOwnerApp(context.packageName) == true }.getOrDefault(false)

    /** "מנהל מכשיר רגיל" פעיל (כולל כשהוא גם Device Owner). */
    fun isAdminActive(context: Context): Boolean =
        runCatching { dpm(context)?.isAdminActive(DeviceAdmin.component(context)) == true }.getOrDefault(false)

    fun alwaysOnVpn(context: Context): Boolean? {
        if (!isDeviceOwner(context)) return null
        return runCatching { dpm(context)?.getAlwaysOnVpnPackage(DeviceAdmin.component(context)) == context.packageName }
            .getOrNull()
    }

    fun alwaysOnLockdown(context: Context): Boolean? {
        if (!isDeviceOwner(context) || Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return null
        return runCatching { dpm(context)?.isAlwaysOnVpnLockdownEnabled(DeviceAdmin.component(context)) }.getOrNull()
    }

    /**
     * מרענן את רשימת החריגים של lockdown לפני חיבור WireGuard.
     * Android חוסם תעבורה שאינה ב-VPN במצב lockdown, אלא אם החבילה ברשימת החריגים.
     * לכן צריך לעדכן את הרשימה גם אחרי עדכון APK/התקנת FilterTube, לפני הקמת המנהרה.
     */
    fun refreshFilterTubeLockdownBypass(context: Context) {
        if (!isDeviceOwner(context) || Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
        val dpm = dpm(context) ?: return
        val admin = DeviceAdmin.component(context)
        if (runCatching { dpm.getAlwaysOnVpnPackage(admin) }.getOrNull() != context.packageName) return

        val lockdown = runCatching { dpm.isAlwaysOnVpnLockdownEnabled(admin) }.getOrNull() ?: return
        val bypassPackages = if (lockdown) {
            VpnConfigFactory.installedFilterTubePackages(context)
        } else {
            emptySet()
        }
        dpm.setAlwaysOnVpnPackage(admin, context.packageName, lockdown, bypassPackages)
    }

    data class Result(val applied: List<String>, val failed: List<String>, val pending: List<String> = emptyList()) {
        fun toJson(): JSONObject = JSONObject().put("applied", JSONArray(applied)).put("failed", JSONArray(failed))
            .put("pending", JSONArray(pending))
    }

    /** ההגדרות האפקטיביות: ברירת המחדל + מה שהשרת שלח. */
    fun effective(dpc: JSONObject?): Map<String, Boolean> =
        DEFAULTS.mapValues { (k, v) -> dpc?.optBoolean(k, v) ?: v }

    /**
     * מחיל את המדיניות. בטלפון שאינו Device Owner — לא עושה כלום (ומחזיר null).
     * בטוח לקריאה חוזרת: כל הגדרה נקבעת למצב הרצוי (גם הסרה של מה שבוטל).
     */
    fun apply(context: Context, policy: GuardPolicy.Policy): Result? {
        if (!isDeviceOwner(context)) return null
        val dpm = dpm(context) ?: return null
        val admin = DeviceAdmin.component(context)
        val want = effective(policy.dpc)
        val paused = AdminState.isPaused(context)   // מצב תחזוקה — נעילת ה-VPN מורפה זמנית
        val applied = mutableListOf<String>()
        val failed = mutableListOf<String>()
        val pending = mutableListOf<String>()
        fun step(name: String, block: () -> Unit) {
            try {
                block()
                applied += name
            } catch (e: Exception) {
                failed += name
                Log.w(TAG, "device owner: $name failed: ${e.javaClass.simpleName}: ${e.message}")
            }
        }

        step("block_uninstall") { dpm.setUninstallBlocked(admin, context.packageName, want.getValue("block_uninstall")) }
        // lockdown: אין חיבור ישיר מחוץ ל-VPN, מלבד FilterPhone ו-FilterTube שהוחרגו במפורש.
        // לפני שיש הגדרות VPN (מיד אחרי ההקמה, לפני ההפעלה בקוד) לא נועלים — ממתינים להגדרות.
        val vpnReady = TunnelManager.isInitialized && TunnelManager.hasConfig()
        if (want.getValue("always_on_vpn") && !vpnReady) {
            pending += "always_on_vpn"
        } else {
            step("always_on_vpn") {
                // דורש ששירות ה-VPN של האפליקציה תומך ב"תמיד מופעל" (WireGuard תומך).
                // בהשהיה (מצב תחזוקה) משאירים VPN תמיד-מופעל אבל בלי נעילה (lockdown=false), כדי שיהיה
                // אינטרנט גם אם מכבים את ה-VPN לרגע. בפקיעת ההשהיה החלה חוזרת ומחזירה את הנעילה.
                if (want.getValue("always_on_vpn")) {
                    val lockdownEnabled = !paused
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                        // Android 10+: גם במצב lockdown, אפשר ל-FilterTube לצאת ישירות מה-VPN.
                        // הרשימה מכילה רק חבילות FilterTube שמותקנות, כדי לא לשבור הקמה.
                        val directNetworkPackages = if (lockdownEnabled) {
                            VpnConfigFactory.installedFilterTubePackages(context)
                        } else {
                            emptySet()
                        }
                        dpm.setAlwaysOnVpnPackage(
                            admin, context.packageName, lockdownEnabled, directNetworkPackages
                        )
                    } else {
                        // Android 9 ומטה לא תומך ברשימת bypass ל-lockdown.
                        dpm.setAlwaysOnVpnPackage(admin, context.packageName, lockdownEnabled)
                    }
                }
                else if (dpm.getAlwaysOnVpnPackage(admin) == context.packageName) dpm.setAlwaysOnVpnPackage(admin, null, false)
            }
        }
        for ((key, restrictions) in RESTRICTIONS) {
            step(key) {
                for (r in restrictions) {
                    if (want[key] == true) dpm.addUserRestriction(admin, r) else dpm.clearUserRestriction(admin, r)
                }
            }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && want["disallow_private_dns"] == true) {
            // מכבה DNS פרטי שכבר הוגדר (ההגבלה רק מונעת שינוי מעכשיו)
            step("private_dns_mode") { dpm.setGlobalPrivateDnsModeOpportunistic(admin) }
        }
        step("hide_blocked_apps") { hideApps(context, dpm, policy, want.getValue("hide_blocked_apps")) }
        step("lock_task_packages") {
            val pk = policy.dpc?.optJSONArray("lock_task_packages")?.let { a -> (0 until a.length()).map { a.optString(it) } }
                ?: emptyList()
            dpm.setLockTaskPackages(admin, (pk + context.packageName).distinct().toTypedArray())
        }
        val frp = policy.dpc?.optJSONArray("frp_accounts")?.let { a -> (0 until a.length()).map { a.optString(it) } }
            ?.filter { it.isNotBlank() } ?: emptyList()
        if (frp.isNotEmpty()) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                step("frp") {
                    dpm.setFactoryResetProtectionPolicy(admin, FactoryResetProtectionPolicy.Builder()
                        .setFactoryResetProtectionAccounts(frp)
                        .setFactoryResetProtectionEnabled(true)
                        .build())
                }
            } else {
                failed += "frp"            // נתמך מאנדרואיד 11 בלבד
            }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            // התראת "ההגנה פעילה" בלי לבקש מהמשתמש
            step("notifications") {
                dpm.setPermissionGrantState(admin, context.packageName, Manifest.permission.POST_NOTIFICATIONS,
                    DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED)
            }
        }
        val result = Result(applied, failed, pending)
        prefs(context).edit().putString(K_RESULT, result.toJson().toString()).apply()
        return result
    }

    /** אפליקציות חסומות (בטלפון) מוסתרות לגמרי; מה שהוסר מהרשימה — חוזר. */
    private fun hideApps(context: Context, dpm: DevicePolicyManager, policy: GuardPolicy.Policy, hide: Boolean) {
        val admin = DeviceAdmin.component(context)
        val p = prefs(context)
        val before = p.getStringSet(K_HIDDEN, emptySet())!!.toSet()
        val wanted = if (hide) policy.apps - GuardPolicy.untouchable(context) else emptySet()
        for (pkg in wanted - before) runCatching { dpm.setApplicationHidden(admin, pkg, true) }
        for (pkg in before - wanted) runCatching { dpm.setApplicationHidden(admin, pkg, false) }
        p.edit().putStringSet(K_HIDDEN, wanted).apply()
    }

    /** ברירת המחדל מיד בסיום ההקמה (לפני שיש מדיניות מהשרת). */
    fun applyBaseline(context: Context): Result? = apply(context, GuardPolicy.get(context))

    /**
     * הסרת ניהול יזומה (רק לפי בקשת השרת, אחרי אישור מנהל): משחרר הגבלות, מבטל "VPN תמיד מופעל",
     * ומסיר את FilterPhone כמנהל המכשיר (clearDeviceOwnerApp — API רשמי). אחרי זה אפשר להסיר/להעביר.
     * לא מוחק נתוני משתמש (לא wipe). נקרא רק מ-Sync בתגובה לפקודת unmanage.
     */
    fun clearManagement(context: Context): Boolean {
        if (!isDeviceOwner(context)) return true
        val dpm = dpm(context) ?: return false
        val admin = DeviceAdmin.component(context)
        // לפני clearDeviceOwnerApp צריך לבטל גם אפליקציות שהוחבאו על ידי DPC; אחרי ההסרה
        // כבר לא ניתן לשנות את מצב ההסתרה שלהן באמצעות DevicePolicyManager.
        runCatching { hideApps(context, dpm, GuardPolicy.get(context), false) }
            .onFailure { Log.w(TAG, "unhide apps before removing management failed", it) }
        runCatching {
            RESTRICTIONS.values.flatten().forEach { dpm.clearUserRestriction(admin, it) }
            if (dpm.getAlwaysOnVpnPackage(admin) == context.packageName) dpm.setAlwaysOnVpnPackage(admin, null, false)
            dpm.setUninstallBlocked(admin, context.packageName, false)
        }
        return try {
            @Suppress("DEPRECATION")
            dpm.clearDeviceOwnerApp(context.packageName)   // ה-API הרשמי להסרת ניהול עצמית
            Log.i(TAG, "device management cleared")
            true
        } catch (e: Exception) {
            Log.w(TAG, "clearDeviceOwnerApp failed", e)
            false
        }
    }

    /** לדיווח לשרת: מה הוחל, מה נכשל, ומצב ה-VPN הקבוע. */
    fun report(context: Context): JSONObject? {
        if (!isDeviceOwner(context)) return null
        val o = prefs(context).getString(K_RESULT, null)?.let { runCatching { JSONObject(it) }.getOrNull() } ?: JSONObject()
        alwaysOnVpn(context)?.let { o.put("always_on_vpn", if (it) "filterphone" else "other") }
        alwaysOnLockdown(context)?.let { o.put("lockdown", it) }
        return o
    }
}
