package com.filterphone.app

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.content.pm.ResolveInfo
import android.os.Build
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * מה שהמנהל חסם בטלפון הזה: אפליקציות שלמות, ומסכים בתוך אפליקציות (למשל צפייה בסטטוסים).
 *
 * הרשימה מגיעה מהשרת — דרך Flutter (setGuardPolicy) כשהאפליקציה פתוחה, ומהשירות עצמו
 * (refreshFromServer) כל חצי שעה, כך ששינוי של המנהל נכנס לתוקף גם בלי לפתוח את האפליקציה.
 * האכיפה:
 *   - GuardService (שירות נגישות): כשאפליקציה חסומה נפתחת — חוזרים למסך הבית;
 *     כשמסך חסום נפתח — "אחורה". עובד בכל טלפון, אחרי שהמשתמש מפעיל את המגן.
 *   - מנהל מכשיר (Device Owner), אם הוגדר: האפליקציות החסומות מוסתרות לגמרי מהמכשיר.
 * הרשימה אינה סוד, ולכן נשמרת ב-SharedPreferences רגיל; אסימון המכשיר נשמר מוצפן.
 */
object GuardPolicy {
    private const val TAG = "FilterPhone"
    private const val PREFS = "fp_guard"
    private const val K_POLICY = "policy"
    private const val K_HIDDEN = "hidden_by_owner"
    private const val K_API = "api"
    private const val K_TOKEN = "guard_token"

    /** אסור לחסום — בלעדיהם הטלפון לא שמיש או שאי אפשר לחייג למוקד חירום. */
    val PROTECTED = setOf(
        "android", "com.android.systemui", "com.android.settings", "com.android.phone",
        "com.android.dialer", "com.google.android.dialer", "com.samsung.android.dialer",
        "com.android.server.telecom", "com.android.emergency", "com.google.android.apps.safetyhub",
    )

    data class Screen(val pkg: String, val match: List<String>)
    data class Policy(val apps: Set<String>, val screens: List<Screen>) {
        val isEmpty get() = apps.isEmpty() && screens.isEmpty()
    }

    enum class Block { APP, SCREEN }

    @Volatile private var cached: Policy? = null
    @Volatile private var untouchable: Set<String> = emptySet()
    @Volatile private var untouchableAt = 0L

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun save(context: Context, json: String) {
        val policy = parse(json)
        prefs(context).edit().putString(K_POLICY, json).apply()
        cached = policy
        applyDeviceOwner(context, policy)
    }

    fun get(context: Context): Policy =
        cached ?: parse(prefs(context).getString(K_POLICY, null)).also { cached = it }

    fun parse(json: String?): Policy {
        if (json.isNullOrBlank()) return Policy(emptySet(), emptyList())
        return try {
            val o = JSONObject(json)
            val apps = o.optJSONArray("blocked_apps")?.let { a -> (0 until a.length()).map { a.optString(it) } }
                ?: emptyList()
            // רשומה פגומה אחת מדלגים עליה — לא מבטלים בגללה את כל הרשימה
            val screens = o.optJSONArray("blocked_screens")?.let { a ->
                (0 until a.length()).mapNotNull { i ->
                    val s = a.optJSONObject(i) ?: return@mapNotNull null
                    val m = s.optJSONArray("match") ?: return@mapNotNull null
                    Screen(s.optString("package"), (0 until m.length()).map { m.optString(it) }.filter { it.isNotBlank() })
                }
            } ?: emptyList()
            Policy(
                apps.filter { it.isNotBlank() && it !in PROTECTED }.toSet(),
                screens.filter { it.pkg.isNotBlank() && it.match.isNotEmpty() },
            )
        } catch (e: Exception) {
            Log.w(TAG, "bad guard policy", e)
            Policy(emptySet(), emptyList())
        }
    }

    /**
     * מה לעשות כשחלון של [pkg] נפתח ([cls] — שם המסך). null = לא חסום.
     * לעולם לא חוסמים את מסך הבית, המקלדת, ההגדרות, החייגן או את FilterPhone עצמה —
     * אחרת "חזרה למסך הבית" הייתה נתקעת בלולאה או שהטלפון היה הופך ללא שמיש.
     */
    fun check(context: Context, policy: Policy, pkg: String, cls: String?): Block? {
        val app = pkg in policy.apps
        val screen = !app && cls != null &&
            policy.screens.any { s -> s.pkg == pkg && s.match.any { cls.contains(it, ignoreCase = true) } }
        if (!app && !screen) return null
        if (pkg in untouchable(context)) return null
        return if (app) Block.APP else Block.SCREEN
    }

    /** חבילות שאסור לגעת בהן: הרשימה הקבועה, האפליקציה הזו, מסך הבית והמקלדת שבשימוש. */
    fun untouchable(context: Context): Set<String> {
        val now = SystemClock.elapsedRealtime()
        if (untouchable.isNotEmpty() && now - untouchableAt < 60_000) return untouchable
        val pm = context.packageManager
        val set = HashSet(PROTECTED)
        set += context.packageName
        val home = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
        runCatching {
            resolveDefault(pm, home)?.activityInfo?.packageName?.let { set += it }
            for (r in query(pm, home)) {
                if (r.activityInfo.applicationInfo.flags and ApplicationInfo.FLAG_SYSTEM != 0) set += r.activityInfo.packageName
            }
        }
        Settings.Secure.getString(context.contentResolver, Settings.Secure.DEFAULT_INPUT_METHOD)
            ?.let { ComponentName.unflattenFromString(it)?.packageName }
            ?.let { set += it }
        untouchable = set
        untouchableAt = now
        return set
    }

    /** מנהל מכשיר: מסתירים את האפליקציות החסומות, ומחזירים את אלה שהוסרו מהרשימה. */
    fun applyDeviceOwner(context: Context, policy: Policy) {
        if (!DeviceAdmin.isDeviceOwner(context)) return
        val dpm = context.getSystemService(DevicePolicyManager::class.java)
        val admin = DeviceAdmin.component(context)
        val before = prefs(context).getStringSet(K_HIDDEN, emptySet())!!.toSet()
        val wanted = policy.apps - untouchable(context)
        for (pkg in wanted - before) runCatching { dpm.setApplicationHidden(admin, pkg, true) }
        for (pkg in before - wanted) runCatching { dpm.setApplicationHidden(admin, pkg, false) }
        prefs(context).edit().putStringSet(K_HIDDEN, wanted).apply()
    }

    /** האפליקציות שאפשר לפתוח מהמסך הראשי — כדי שהמנהל יוכל לחסום כל אחת בלחיצה. */
    fun installedApps(context: Context): List<Map<String, Any>> {
        val pm = context.packageManager
        val skip = untouchable(context)
        return query(pm, Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER))
            .map { it.activityInfo }
            .distinctBy { it.packageName }
            .filter { it.packageName !in skip }
            .map { ai ->
                mapOf(
                    "package" to ai.packageName,
                    "label" to ai.loadLabel(pm).toString(),
                    "system" to ((ai.applicationInfo.flags and ApplicationInfo.FLAG_SYSTEM) != 0),
                )
            }
            .sortedBy { (it["label"] as String).lowercase() }
    }

    /** האם המשתמש הפעיל את שירות הנגישות של FilterPhone. */
    fun isServiceEnabled(context: Context): Boolean {
        val enabled = Settings.Secure.getString(
            context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false
        val me = ComponentName(context, GuardService::class.java)
        return enabled.split(':').any { ComponentName.unflattenFromString(it) == me }
    }

    // ------------------------------------------------------------ רענון מהשרת (בלי Flutter)

    /** הכתובת והאסימון של המכשיר, כדי שהשירות יוכל לרענן לבד. token=null — מחיקה. */
    fun setCredentials(context: Context, api: String?, token: String?) {
        if (api.isNullOrBlank() || token.isNullOrBlank()) {
            prefs(context).edit().remove(K_API).apply()
            SecureStore(context).put(K_TOKEN, null)
            return
        }
        prefs(context).edit().putString(K_API, api.trimEnd('/')).apply()
        SecureStore(context).put(K_TOKEN, token)
    }

    /**
     * מושך את רשימת החסימות העדכנית מהשרת (GET /v1/device). בכל תקלה — משאירים את הקיימת:
     * עדיף חסימה ישנה מאשר פתיחה בגלל רשת חלשה.
     */
    fun refreshFromServer(context: Context): Boolean {
        // רץ ב-thread של השירות: חריגה שלא נתפסת הייתה מפילה את כל האפליקציה — לכן הכול בתוך try
        var conn: HttpURLConnection? = null
        return try {
            val api = prefs(context).getString(K_API, null) ?: return false
            val token = SecureStore(context).get(K_TOKEN) ?: return false
            conn = (URL("$api/v1/device").openConnection() as HttpURLConnection).apply {
                connectTimeout = 15_000
                readTimeout = 15_000
                setRequestProperty("Authorization", "Bearer $token")
                setRequestProperty("Accept", "application/json")
            }
            if (conn.responseCode != HttpURLConnection.HTTP_OK) return false
            val body = conn.inputStream.bufferedReader().use { it.readText() }
            val o = JSONObject(body)
            if (o.optString("status", "active") != "active") return false
            val json = o.optJSONObject("device_policy")?.toString() ?: return false
            if (parse(json) != get(context)) save(context, json)
            true
        } catch (e: Exception) {
            Log.i(TAG, "guard refresh skipped: ${e.javaClass.simpleName}")
            false
        } finally {
            conn?.disconnect()
        }
    }

    private fun query(pm: PackageManager, intent: Intent): List<ResolveInfo> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            pm.queryIntentActivities(intent, PackageManager.ResolveInfoFlags.of(0))
        } else {
            @Suppress("DEPRECATION")
            pm.queryIntentActivities(intent, 0)
        }

    private fun resolveDefault(pm: PackageManager, intent: Intent): ResolveInfo? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            pm.resolveActivity(intent, PackageManager.ResolveInfoFlags.of(PackageManager.MATCH_DEFAULT_ONLY.toLong()))
        } else {
            @Suppress("DEPRECATION")
            pm.resolveActivity(intent, PackageManager.MATCH_DEFAULT_ONLY)
        }
}
