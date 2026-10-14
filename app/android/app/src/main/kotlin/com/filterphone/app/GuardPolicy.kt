package com.filterphone.app

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
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

/**
 * מה שהמנהל חסם בטלפון הזה: אפליקציות שלמות, מסכים בתוך אפליקציות (למשל צפייה בסטטוסים),
 * הגנות מיוחדות (תמונות פרופיל בוואטסאפ) והגדרות מנהל מכשיר.
 *
 * מקורות: Flutter (setGuardPolicy) כשהאפליקציה פתוחה, והדיווח התקופתי (Sync) גם כשהיא סגורה.
 * לכל מדיניות גרסה מהשרת — גרסה ישנה לא דורסת חדשה, והטלפון מאשר לשרת איזו גרסה הוחלה.
 *
 * לעולם לא "נפתח" בגלל תקלה: רשימה פגומה לא נשמרת (נשארת הקודמת), אין רשת — נשארת השמורה,
 * והעותק האחרון שעבד נשמר בנפרד ליתר ביטחון.
 */
object GuardPolicy {
    private const val TAG = "FilterPhone"
    private const val PREFS = "fp_guard"
    private const val K_POLICY = "policy"
    private const val K_POLICY_GOOD = "policy_last_good"
    private const val K_API = "api"
    private const val K_TOKEN = "guard_token"
    private const val K_ERROR = "policy_error"

    /** אסור לחסום — בלעדיהם הטלפון לא שמיש או שאי אפשר לחייג למוקד חירום. */
    val PROTECTED = setOf(
        "android", "com.android.systemui", "com.android.settings", "com.android.phone",
        "com.android.dialer", "com.google.android.dialer", "com.samsung.android.dialer",
        "com.android.server.telecom", "com.android.emergency", "com.google.android.apps.safetyhub",
    )

    data class Screen(val pkg: String, val match: List<String>)

    /** זיהוי מסך תמונת פרופיל בוואטסאפ — התבניות מגיעות מהשרת (עם ברירת מחדל זהה כאן). */
    data class WaDetector(
        val packages: Set<String>,
        val activities: List<String>,
        val viewIds: List<String>,
        val descriptions: List<String>,
        val minArea: Float,
        val excludeWindows: List<String> = listOf("mediaview", "gallery", "camera", "status"),
    ) {
        companion object {
            val DEFAULT = WaDetector(
                packages = setOf("com.whatsapp", "com.whatsapp.w4b"),
                activities = listOf("viewprofilephoto", "profilephoto", "profilepicture", "quickcontact", "photo.viewprofile"),
                viewIds = listOf("profile_picture", "profile_photo", "contact_photo", "quick_contact_photo",
                    "wds_profile_photo", "profile_picture_image"),
                descriptions = listOf("profile photo", "profile picture", "תמונת פרופיל", "תמונת הפרופיל",
                    "foto de perfil", "photo de profil", "profilbild"),
                minArea = 0.3f,
            )
        }
    }

    /** חסימת מילות חיפוש: רשימת מונחים מהשרת (כבר מנורמלת) + האם פעיל. null = כבוי. */
    data class KeywordRule(val terms: List<String>, val action: String = "back")

    data class Policy(
        val apps: Set<String>,
        val screens: List<Screen>,
        val version: Int = 0,
        val checksum: String = "",
        val waProfile: WaDetector? = null,
        val keywords: KeywordRule? = null,
        val dpc: JSONObject? = null,
        /** אפליקציות מחוץ ל-VPN (FilterTube וכו'). שרת שעוד לא שולח את השדה → ברירת המחדל. */
        val vpnBypass: List<VpnBypass.App> = VpnBypass.DEFAULTS,
    ) {
        val isEmpty get() = apps.isEmpty() && screens.isEmpty() && waProfile == null
    }

    enum class Block { APP, SCREEN }

    private val EMPTY = Policy(emptySet(), emptyList())

    @Volatile private var cached: Policy? = null
    @Volatile private var untouchable: Set<String> = emptySet()
    @Volatile private var untouchableAt = 0L

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /**
     * שומר מדיניות חדשה. מחזיר false אם היא פגומה (אז נשארת הקודמת). גרסה ישנה מהנוכחית — מתעלמים
     * (למשל תשובה ישנה מהשרת שהגיעה אחרי חדשה), ומחזירים true.
     */
    fun save(context: Context, json: String): Boolean {
        val policy = try {
            parseStrict(json)
        } catch (e: Exception) {
            Log.w(TAG, "rejected bad guard policy: ${e.javaClass.simpleName}")
            prefs(context).edit().putString(K_ERROR, "invalid policy: ${e.javaClass.simpleName}").apply()
            return false
        }
        val current = get(context)
        if (policy.version > 0 && current.version > policy.version) return true
        prefs(context).edit().putString(K_POLICY, json).putString(K_POLICY_GOOD, json).remove(K_ERROR).commit()
        cached = policy
        ProtectionState.mark(context, ProtectionState.POLICY_SYNC)
        runCatching { DeviceOwnerManager.apply(context, policy) }.onFailure { Log.w(TAG, "device owner apply failed", it) }
        GuardService.onPolicyChanged()
        // רשימת "מחוץ ל-VPN" אולי השתנתה — המנהרה נבנית מחדש רק אם יש הבדל
        runCatching { if (TunnelManager.isInitialized) TunnelManager.refreshBypass("policy") }
        return true
    }

    fun get(context: Context): Policy {
        cached?.let { return it }
        val p = prefs(context)
        for (key in arrayOf(K_POLICY, K_POLICY_GOOD)) {
            val raw = p.getString(key, null) ?: continue
            try {
                return parseStrict(raw).also { cached = it }
            } catch (e: Exception) {
                Log.w(TAG, "stored policy ($key) unreadable: ${e.javaClass.simpleName}")
            }
        }
        return EMPTY.also { cached = it }
    }

    fun version(context: Context): Int = get(context).version

    fun lastError(context: Context): String? = prefs(context).getString(K_ERROR, null)

    /** פירוש לבדיקות ולתאימות: שגיאה → מדיניות ריקה. לשמירה — רק parseStrict. */
    fun parse(json: String?): Policy =
        if (json.isNullOrBlank()) EMPTY else try { parseStrict(json) } catch (e: Exception) { EMPTY }

    /**
     * פירוש קפדני: JSON לא תקין או שדה עליון מסוג שגוי — חריגה (והמדיניות הקודמת נשארת).
     * רשומה פגומה אחת בתוך רשימה — מדלגים עליה, בלי לבטל את כל השאר.
     */
    @Throws(JSONException::class)
    fun parseStrict(json: String): Policy {
        val o = JSONObject(json)
        val apps = o.optArrayStrict("blocked_apps")?.let { a -> (0 until a.length()).mapNotNull { a.opt(it) as? String } }
            ?: emptyList()
        val screens = o.optArrayStrict("blocked_screens")?.let { a ->
            (0 until a.length()).mapNotNull { i ->
                val s = a.optJSONObject(i) ?: return@mapNotNull null
                val m = s.optJSONArray("match") ?: return@mapNotNull null
                Screen(s.optString("package"), (0 until m.length()).map { m.optString(it) }.filter { it.isNotBlank() })
            }
        } ?: emptyList()
        val protections = o.opt("protections")
        if (protections != null && protections != JSONObject.NULL && protections !is JSONObject) {
            throw JSONException("protections is not an object")
        }
        val wa = (protections as? JSONObject)?.let { parseWa(it.opt("wa_profile_photo")) }
        val dpc = o.optJSONObject("dpc")
        return Policy(
            apps.filter { it.isNotBlank() && it !in PROTECTED }.toSet(),
            screens.filter { it.pkg.isNotBlank() && it.match.isNotEmpty() },
            version = o.optInt("version", 0),
            checksum = o.optString("checksum", ""),
            waProfile = wa,
            keywords = parseKeywords(o.opt("keywords")),
            dpc = dpc,
            vpnBypass = VpnBypass.parse(o.opt("vpn_bypass")),
        )
    }

    private fun JSONObject.optArrayStrict(key: String): JSONArray? {
        val v = opt(key) ?: return null
        if (v == JSONObject.NULL) return null
        return v as? JSONArray ?: throw JSONException("$key is not an array")
    }

    private fun parseWa(raw: Any?): WaDetector? {
        if (raw == null || raw == JSONObject.NULL || raw == false) return null
        if (raw == true) return WaDetector.DEFAULT
        val o = raw as? JSONObject ?: return WaDetector.DEFAULT
        fun list(key: String, fallback: List<String>): List<String> {
            val a = o.optJSONArray(key) ?: return fallback
            return (0 until a.length()).map { a.optString(it).trim().lowercase() }.filter { it.isNotEmpty() }
                .ifEmpty { fallback }
        }
        val d = WaDetector.DEFAULT
        return WaDetector(
            packages = list("packages", d.packages.toList()).toSet(),
            activities = list("activities", d.activities),
            viewIds = list("view_ids", d.viewIds),
            descriptions = list("descriptions", d.descriptions),
            minArea = o.optDouble("min_area", d.minArea.toDouble()).toFloat().coerceIn(0.05f, 0.95f),
            excludeWindows = list("exclude_windows", d.excludeWindows),
        )
    }

    /** חסימת מילות חיפוש: רק אם פעיל ויש מונחים. גרסה ישנה של השרת (בלי השדה) → null. */
    private fun parseKeywords(raw: Any?): KeywordRule? {
        val o = raw as? JSONObject ?: return null
        if (!o.optBoolean("enabled", false)) return null
        val a = o.optJSONArray("terms") ?: return null
        val terms = (0 until a.length()).map { a.optString(it).trim() }.filter { it.isNotEmpty() }
        if (terms.isEmpty()) return null
        return KeywordRule(terms, o.optString("action", "back"))
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

    /** האם המשתמש הפעיל את שירות הנגישות של FilterPhone (בהגדרות). */
    fun isServiceEnabled(context: Context): Boolean {
        val enabled = Settings.Secure.getString(
            context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false
        val me = ComponentName(context, GuardService::class.java)
        return enabled.split(':').any { ComponentName.unflattenFromString(it) == me }
    }

    // ------------------------------------------------------------ פרטי הגישה לשרת (לרענון בלי Flutter)

    /** הכתובת והאסימון של המכשיר, כדי שהדיווח התקופתי יעבוד גם כשהאפליקציה סגורה. null — מחיקה. */
    fun setCredentials(context: Context, api: String?, token: String?) {
        if (api.isNullOrBlank() || token.isNullOrBlank()) {
            prefs(context).edit().remove(K_API).apply()
            SecureStore(context).put(K_TOKEN, null)
            return
        }
        prefs(context).edit().putString(K_API, api.trimEnd('/')).apply()
        SecureStore(context).put(K_TOKEN, token)
    }

    fun credentials(context: Context): Pair<String, String>? {
        val api = prefs(context).getString(K_API, null) ?: return null
        val token = SecureStore(context).get(K_TOKEN) ?: return null
        return api to token
    }

    /** מחיקה מלאה (איפוס המכשיר מהאפליקציה). */
    fun clear(context: Context) {
        prefs(context).edit().clear().commit()
        cached = null
        SecureStore(context).put(K_TOKEN, null)
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
