package com.filterphone.app

import android.Manifest
import android.app.admin.DevicePolicyManager
import android.app.admin.FactoryResetProtectionPolicy
import android.content.Context
import android.content.pm.PackageManager
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
    private const val K_SUSPENDED = "suspended_by_owner"
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
        "suspend_blocked_apps" to true,     // אפור + "חסום על ידי המנהל" — גובר על ההסתרה
        "disallow_tethering" to true,       // נקודה חמה / שיתוף אינטרנט (Wi-Fi, בלוטות', USB)
    )

    /**
     * השהיה שהמנהל אישר = הטלפון פתוח לגמרי: בלי VPN, בלי הגבלות, אפליקציות חסומות חוזרות.
     * רק ההגנה מפני הסרת FilterPhone נשארת — להסרה מלאה יש כפתור מפורש (עם אזהרה).
     */
    private val PAUSED: Map<String, Boolean> = DEFAULTS.mapValues { (k, _) -> k == "block_uninstall" }

    private enum class AppMode { NONE, HIDE, SUSPEND }

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
            put("disallow_tethering", listOf(UserManager.DISALLOW_CONFIG_TETHERING))
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
     * רשימת ההיתר של "חסימה בלי VPN" (lockdown) = האפליקציות שמחוץ למנהרה (VpnBypass). אחרת אנדרואיד
     * חוסם להן את הרשת. נקרא לפני כל הקמה של המנהרה. אנדרואיד 10+ בלבד; משנה רק כשיש הבדל.
     */
    fun refreshLockdownAllowlist(context: Context, packages: Set<String>) {
        if (!isDeviceOwner(context) || Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
        val dpm = dpm(context) ?: return
        val admin = DeviceAdmin.component(context)
        if (dpm.getAlwaysOnVpnPackage(admin) != context.packageName || !dpm.isAlwaysOnVpnLockdownEnabled(admin)) return
        val current = runCatching { dpm.getAlwaysOnVpnLockdownWhitelist(admin) }.getOrNull().orEmpty()
        if (current == packages) return
        setAlwaysOn(dpm, admin, context.packageName, lockdown = true, packages)
    }

    /** אנדרואיד 9 ומטה: lockdown בלי רשימת היתר — אפליקציה שמחוץ ל-VPN נשארת בלי רשת (מדווח לשרת). */
    fun lockdownConflict(context: Context, excluded: Set<String>): Boolean =
        excluded.isNotEmpty() && Build.VERSION.SDK_INT < Build.VERSION_CODES.Q && isDeviceOwner(context) &&
            effective(GuardPolicy.get(context).dpc).getValue("always_on_vpn") && !AdminState.isPaused(context)

    /** VPN תמיד מופעל; עם רשימת היתר (10+). חבילה שהוסרה ברגע זה — מנסים שוב בלי הרשימה (לא נכשלים). */
    private fun setAlwaysOn(dpm: DevicePolicyManager, admin: android.content.ComponentName, pkg: String,
                            lockdown: Boolean, allow: Set<String>) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            dpm.setAlwaysOnVpnPackage(admin, pkg, lockdown)
            return
        }
        try {
            dpm.setAlwaysOnVpnPackage(admin, pkg, lockdown, if (lockdown) allow else emptySet())
        } catch (e: PackageManager.NameNotFoundException) {
            Log.w(TAG, "lockdown allowlist: a package is gone — applying without it")
            dpm.setAlwaysOnVpnPackage(admin, pkg, lockdown, emptySet())
        }
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
        val paused = AdminState.isPaused(context)   // השהיה שהמנהל אישר — הטלפון פתוח (PAUSED)
        val want = if (paused) PAUSED else effective(policy.dpc)
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

        step("block_uninstall") {
            dpm.setUninstallBlocked(admin, context.packageName, want.getValue("block_uninstall"))
            // אנדרואיד 11+: גם "עצירה בכוח" וניקוי נתונים של FilterPhone חסומים (אחרת עוצרים את המגן)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                dpm.setUserControlDisabledPackages(admin,
                    if (want.getValue("block_uninstall")) listOf(context.packageName) else emptyList())
            }
        }
        // lockdown: אין חיבור ישיר מחוץ ל-VPN, מלבד FilterPhone והאפליקציות שהוחרגו במפורש (VpnBypass).
        // לפני שיש הגדרות VPN (מיד אחרי ההקמה, לפני ההפעלה בקוד) לא נועלים — ממתינים להגדרות.
        // בהשהיה: "תמיד מופעל" מבוטל לגמרי (ה-VPN כבוי) וחוזר בסוף ההשהיה.
        val vpnReady = TunnelManager.isInitialized && TunnelManager.hasConfig()
        if (want.getValue("always_on_vpn") && !vpnReady) {
            pending += "always_on_vpn"
        } else {
            step("always_on_vpn") {
                if (want.getValue("always_on_vpn")) {
                    val bypass = runCatching { VpnBypass.current(context).excluded }.getOrDefault(emptySet())
                    setAlwaysOn(dpm, admin, context.packageName, lockdown = true, bypass)
                } else if (dpm.getAlwaysOnVpnPackage(admin) == context.packageName) {
                    dpm.setAlwaysOnVpnPackage(admin, null, false)
                }
            }
        }
        // התקנה שקטה באמצע (עדכון עצמי / חנות): חסימת ההתקנות חוזרת כשהיא מסתיימת, לא עכשיו
        val installWindow = runCatching { SelfUpdate.installWindowOpen(context) || ManagedApps.installWindowOpen(context) }
            .getOrDefault(false)
        for ((key, restrictions) in RESTRICTIONS) {
            if (key == "disallow_install_apps" && want[key] == true && installWindow) {
                pending += key
                continue
            }
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
        step("hide_blocked_apps") { applyBlockedApps(context, dpm, policy, appMode(want)) }
        step("support_message") {
            dpm.setShortSupportMessage(admin, context.getString(R.string.admin_support_short))
            dpm.setLongSupportMessage(admin, context.getString(R.string.admin_support_long))
        }
        // במקום "המכשיר הזה שייך לארגון שלך" — "המכשיר הזה שייך ל-פילטר פון" (מסך הנעילה ופס ההתראות)
        step("organization_name") { dpm.setOrganizationName(admin, organizationName(context)) }
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

    private fun appMode(want: Map<String, Boolean>): AppMode = when {
        want["suspend_blocked_apps"] == true -> AppMode.SUSPEND
        want["hide_blocked_apps"] == true -> AppMode.HIDE
        else -> AppMode.NONE
    }

    /**
     * אפליקציות חסומות (בטלפון): "אפור" (setPackagesSuspended — האייקון נשאר, לחיצה מציגה "חסום על ידי
     * המנהל" עם ההודעה שלנו) או מוסתרות לגמרי. מה שהוסר מהרשימה — חוזר. אפליקציה שעוד לא מותקנת לא
     * נרשמת כ"הוחלה", כדי שתיחסם ברגע שתותקן (PackageWatch / הסנכרון התקופתי).
     */
    private fun applyBlockedApps(context: Context, dpm: DevicePolicyManager, policy: GuardPolicy.Policy, mode: AppMode) {
        val admin = DeviceAdmin.component(context)
        val p = prefs(context)
        val hiddenBefore = p.getStringSet(K_HIDDEN, emptySet())!!.toSet()
        val suspendedBefore = p.getStringSet(K_SUSPENDED, emptySet())!!.toSet()
        val blocked = policy.apps - GuardPolicy.untouchable(context)
        val wantHidden = if (mode == AppMode.HIDE) blocked else emptySet()
        val wantSuspended = if (mode == AppMode.SUSPEND) blocked else emptySet()
        for (pkg in hiddenBefore - wantHidden) runCatching { dpm.setApplicationHidden(admin, pkg, false) }
        (suspendedBefore - wantSuspended).takeIf { it.isNotEmpty() }?.let { off ->
            runCatching { dpm.setPackagesSuspended(admin, off.toTypedArray(), false) }
        }
        val hidden = wantHidden.filterTo(HashSet()) { pkg ->
            runCatching { dpm.setApplicationHidden(admin, pkg, true) || dpm.isApplicationHidden(admin, pkg) }
                .getOrDefault(false)
        }
        val notSuspended = if (wantSuspended.isEmpty()) emptySet() else
            runCatching { dpm.setPackagesSuspended(admin, wantSuspended.toTypedArray(), true).toSet() }
                .getOrDefault(wantSuspended)
        p.edit().putStringSet(K_HIDDEN, hidden).putStringSet(K_SUSPENDED, wantSuspended - notSuspended).apply()
    }

    /** מה שהשרת קבע (דשבורד → מערכת → שם הארגון), ואם עוד לא הגיע — ברירת המחדל. */
    fun organizationName(context: Context): String =
        ProtectionState.string(context, ProtectionState.ORG_NAME)?.takeIf { it.isNotBlank() }
            ?: context.getString(R.string.organization_name)

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
        // לפני clearDeviceOwnerApp: מחזירים אפליקציות שהוסתרו/הושבתו — אחרי ההסרה כבר אי אפשר.
        runCatching { applyBlockedApps(context, dpm, GuardPolicy.get(context), AppMode.NONE) }
            .onFailure { Log.w(TAG, "restore blocked apps before removing management failed", it) }
        runCatching {
            RESTRICTIONS.values.flatten().forEach { dpm.clearUserRestriction(admin, it) }
            if (dpm.getAlwaysOnVpnPackage(admin) == context.packageName) dpm.setAlwaysOnVpnPackage(admin, null, false)
            dpm.setUninstallBlocked(admin, context.packageName, false)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) dpm.setUserControlDisabledPackages(admin, emptyList())
            dpm.setShortSupportMessage(admin, null)
            dpm.setLongSupportMessage(admin, null)
            dpm.setOrganizationName(admin, null)
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
