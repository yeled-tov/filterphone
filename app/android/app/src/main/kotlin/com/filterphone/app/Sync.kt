package com.filterphone.app

import android.app.ActivityManager
import android.app.NotificationManager
import android.app.job.JobParameters
import android.app.job.JobService
import android.content.Context
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.util.Log
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * הדיווח התקופתי לשרת (heartbeat) — גם כשהאפליקציה סגורה:
 * מצב ה-VPN, הנגישות, מנהל המכשיר, הסוללה, וגרסת המדיניות שהוחלה. בתשובה — מדיניות חדשה אם יש.
 * שום תוכן מהטלפון: לא הודעות, לא אפליקציות שנפתחו, לא היסטוריה.
 */
object Sync {
    private const val TAG = "FilterPhone"

    /** מצב ההגנה — בדיוק השדות שהשרת מכיר (sanitize_heartbeat בשרת). */
    fun collect(context: Context): JSONObject {
        val pm = context.packageManager
        val power = context.getSystemService(PowerManager::class.java)
        val am = context.getSystemService(ActivityManager::class.java)
        val vpn = JSONObject().put("state", TunnelManager.state.name)
        TunnelManager.handshakeAgeMs()?.let { vpn.put("handshake_age_s", it / 1000) }
        TunnelManager.alwaysOn()?.let { vpn.put("always_on", it); ProtectionState.setString(context, "always_on", it.toString()) }
            ?: ProtectionState.string(context, "always_on")?.let { vpn.put("always_on", it == "true") }
        TunnelManager.lockdown()?.let { vpn.put("lockdown", it); ProtectionState.setString(context, "lockdown", it.toString()) }
            ?: ProtectionState.string(context, "lockdown")?.let { vpn.put("lockdown", it == "true") }
        ProtectionState.time(context, ProtectionState.VPN_HEALTH_OK).takeIf { it > 0 }?.let { vpn.put("last_health_ok", it / 1000) }
        vpn.put("reconnects", ProtectionState.count(context, ProtectionState.VPN_RECONNECTS))
        ProtectionState.string(context, ProtectionState.VPN_ERROR)?.let { vpn.put("error", it.take(80)) }

        val policy = GuardPolicy.get(context)
        val needsAccessibility = policy.screens.isNotEmpty() || policy.waProfile != null || policy.keywords != null ||
            (policy.apps.isNotEmpty() && !DeviceOwnerManager.isDeviceOwner(context))
        val accessibility = JSONObject()
            .put("enabled", GuardPolicy.isServiceEnabled(context))
            .put("running", GuardService.isRunning)
            .put("required", needsAccessibility)
            .put("self_heal", AccessibilityGuard.canHeal(context))
            .put("restored", ProtectionState.count(context, ProtectionState.GUARD_RESTORED))

        val o = JSONObject()
            .put("app_version", runCatching { pm.getPackageInfo(context.packageName, 0).versionName }.getOrNull() ?: "")
            .put("sdk", Build.VERSION.SDK_INT)
            .put("device_uid", DeviceId.get(context))
            .put("policy_version", policy.version)
            .put("vpn", vpn)
            .put("accessibility", accessibility)
            .put("device_owner", DeviceOwnerManager.isDeviceOwner(context))
            .put("device_admin", DeviceOwnerManager.isAdminActive(context))
            .put("tamper", JSONObject()
                .put("adb", Settings.Global.getInt(context.contentResolver, Settings.Global.ADB_ENABLED, 0) == 1)
                .put("dev_options", Settings.Global.getInt(context.contentResolver,
                    Settings.Global.DEVELOPMENT_SETTINGS_ENABLED, 0) == 1)
                .put("safe_mode", pm.isSafeMode))
            .put("protections", JSONObject()
                .put("apps_blocked", ProtectionState.count(context, ProtectionState.APPS_BLOCKED))
                .put("screens_blocked", ProtectionState.count(context, ProtectionState.SCREENS_BLOCKED))
                .put("wa_profile_photo", ProtectionState.count(context, ProtectionState.WA_PHOTOS_BLOCKED))
                .put("keywords_blocked", ProtectionState.count(context, ProtectionState.KEYWORDS_BLOCKED)))
        power?.let { o.put("battery_unrestricted", it.isIgnoringBatteryOptimizations(context.packageName)) }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) am?.let { o.put("background_restricted", it.isBackgroundRestricted) }
        context.getSystemService(NotificationManager::class.java)?.let { o.put("notifications", it.areNotificationsEnabled()) }
        o.put("private_dns", Settings.Global.getString(context.contentResolver, "private_dns_mode") ?: "off")
        ProtectionState.string(context, ProtectionState.CA_PEM)?.let { pem ->
            runCatching { CertHelper.isInstalled(pem) }.getOrNull()?.let { o.put("ca_installed", it) }
        }
        DeviceOwnerManager.report(context)?.let { o.put("dpc", it) }
        runCatching {
            val decision = VpnBypass.current(context)
            o.put("vpn_bypass", VpnBypass.report(TunnelManager.appliedBypass, decision,
                DeviceOwnerManager.lockdownConflict(context, decision.excluded)))
        }
        SelfUpdate.report(context)?.let { o.put("update", it) }
        runCatching {
            val caps = Preflight.capabilities(context)
            o.put("capabilities", JSONObject(caps.toMap()))
            o.put("preflight", Preflight.evaluate(caps).first.name)
        }
        GuardPolicy.lastError(context)?.let { o.put("policy_error", it) }
        ProtectionState.time(context, ProtectionState.POLICY_SYNC).takeIf { it > 0 }
            ?.let { o.put("last_policy_sync", it / 1000) }
        o.put("ts", System.currentTimeMillis() / 1000)
        return o
    }

    /**
     * שולח דיווח; אם השרת מחזיר מדיניות חדשה — מחיל ושולח אישור (פעם אחת, בלי לולאה).
     * מחזיר true אם השרת ענה.
     */
    fun heartbeat(context: Context): Boolean {
        val (api, token) = GuardPolicy.credentials(context) ?: return false
        runCatching { AccessibilityGuard.ensureEnabled(context, "sync") }
        val first = post(context, "$api/v1/device/heartbeat", token, collect(context)) ?: return false
        val policy = first.optJSONObject("policy")
        if (policy != null && first.optString("status") == "active") {
            if (GuardPolicy.save(context, policy.toString())) {
                // אישור מיידי: הממשק יראה "מסונכרן" בלי לחכות 15 דקות
                post(context, "$api/v1/device/heartbeat", token, collect(context))
            }
        } else if (first.optInt("policy_version", -1) == GuardPolicy.version(context)) {
            ProtectionState.mark(context, ProtectionState.POLICY_SYNC)
        }
        // גרסה חדשה של האפליקציה: במנהל מכשיר — עדכון שקט ובטוח; אחרת — הודעה במסך הראשי
        if (first.optString("status") == "active") {
            runCatching { SelfUpdate.consider(context, first.optJSONObject("app_update")) }
                .onFailure { Log.w(TAG, "self-update check failed", it) }
            // התקנה מהחנות שהתוצאה שלה לא הגיעה — מחזירים את חסימת ההתקנות
            runCatching { ManagedApps.restoreRestrictions(context, staleOnly = true) }
            // קטלוג מאושר: אפליקציות "חובה" חסרות מותקנות בשקט (רק במנהל מכשיר)
            runCatching { ManagedApps.sync(context, first.optJSONArray("managed_apps")) }
                .onFailure { Log.w(TAG, "managed apps sync failed", it) }
        }
        // פקודת מנהל: השהיית הגנה (הטלפון פתוח עד סוף התוקף) או הסרת ניהול — מהערוץ המאומת בלבד
        runCatching {
            val wasPaused = AdminState.isPaused(context)
            val action = AdminState.apply(context, first.optJSONObject("admin"), first.optLong("server_time"))
            when {
                action == AdminState.Action.UNMANAGE -> DeviceOwnerManager.clearManagement(context)
                AdminState.isPaused(context) != wasPaused -> Recovery.onPauseChanged(context)
                else -> DeviceOwnerManager.apply(context, GuardPolicy.get(context))
            }
            GuardService.onPolicyChanged()
        }.onFailure { Log.w(TAG, "admin command failed", it) }
        return true
    }

    /** בקשה קצרה לשרת בשם המכשיר (למשל "החזר הגנה" / "הסרת הכול"). true אם השרת אישר. */
    fun notify(context: Context, path: String): Boolean {
        val (api, token) = GuardPolicy.credentials(context) ?: return false
        return post(context, "$api$path", token, JSONObject())?.optBoolean("ok", false) == true
    }

    private fun post(context: Context, url: String, token: String, body: JSONObject): JSONObject? {
        var conn: HttpURLConnection? = null
        return try {
            conn = (URL(url).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 15_000
                readTimeout = 15_000
                doOutput = true
                setRequestProperty("Authorization", "Bearer $token")
                setRequestProperty("Content-Type", "application/json")
                setRequestProperty("Accept", "application/json")
            }
            conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            if (code != HttpURLConnection.HTTP_OK) {
                ProtectionState.setString(context, ProtectionState.HEARTBEAT_ERROR, "http $code")
                return null
            }
            val text = conn.inputStream.bufferedReader().use { it.readText() }
            ProtectionState.mark(context, ProtectionState.HEARTBEAT_OK)
            ProtectionState.setString(context, ProtectionState.HEARTBEAT_ERROR, null)
            JSONObject(text)
        } catch (e: Exception) {
            ProtectionState.setString(context, ProtectionState.HEARTBEAT_ERROR, e.javaClass.simpleName)
            Log.i(TAG, "heartbeat skipped: ${e.javaClass.simpleName}")
            null
        } finally {
            conn?.disconnect()
        }
    }
}

/**
 * המשימה של JobScheduler: תקופתית (כל 15 דקות), "עכשיו", וניסיון חיבור VPN חוזר.
 * רצה גם כשהאפליקציה סגורה ושורדת אתחול. עבודה ברקע — לא ב-main thread.
 */
class SyncJob : JobService() {
    override fun onStartJob(params: JobParameters): Boolean {
        val app = applicationContext
        executor.execute {
            try {
                TunnelManager.init(app)
                if (params.jobId == Recovery.JOB_RESUME) Recovery.onPauseChanged(app)   // סוף ההשהיה
                TunnelManager.ensureConnected(if (params.jobId == Recovery.JOB_RETRY) "retry" else "job")
                if (params.jobId != Recovery.JOB_RETRY) {
                    TunnelManager.healthCheck()
                    Recovery.startProtection(app)        // ייתכן שאנדרואיד יסרב מהרקע — זה בסדר
                    Sync.heartbeat(app)
                    // מנהל מכשיר: יישור ההגדרות (למשל נעילת VPN אחרי שההגדרות הגיעו)
                    runCatching { DeviceOwnerManager.apply(app, GuardPolicy.get(app)) }
                }
            } catch (e: Exception) {
                Log.w("FilterPhone", "sync job failed", e)
            } finally {
                jobFinished(params, false)
            }
        }
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean = params.jobId == Recovery.JOB_RETRY

    private companion object {
        val executor = Executors.newSingleThreadExecutor { r -> Thread(r, "fp-sync").apply { isDaemon = true } }
    }
}
