package com.filterphone.app

import android.app.PendingIntent
import android.app.admin.DevicePolicyManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.os.Build
import android.os.UserManager
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * קטלוג אפליקציות מאושרות. במנהל מכשיר (Device Owner) אפליקציות שסומנו "חובה" ושיש להן קובץ APK
 * שמתארח אצלנו מותקנות אוטומטית ובשקט (PackageInstaller — API רשמי), אחרי בדיקת SHA-256 ושם חבילה.
 *
 * מה שאי אפשר כאן: התקנה שקטה מ-Google Play דורשת רישום ארגוני (Managed Google Play / Android
 * Management API) מול גוגל — זה מחוץ לתחום, ואפליקציות כאלה בקטלוג מסומנות "התקנה ידנית מהחנות"
 * (האפליקציה מפנה את המשתמש, לא מזייפת מקור התקנה).
 *
 * ללא מנהל מכשיר אין התקנה שקטה — הקטלוג רק מוצג (ה-UI של הלקוח).
 */
object ManagedApps {
    private const val TAG = "FilterPhone"
    const val ACTION_RESULT = "com.filterphone.app.MANAGED_INSTALL_RESULT"
    const val EXTRA_PACKAGE = "com.filterphone.app.extra.PACKAGE"
    private const val MAX_BYTES = 300L * 1024 * 1024
    private val SHA = Regex("^[0-9a-f]{64}$")

    data class App(val pkg: String, val url: String, val sha256: String, val versionCode: Long)

    /** האפליקציות מהקטלוג שאפשר להתקין אוטומטית: חובה, עם APK שמתארח אצלנו ונתונים תקינים. טהור. */
    fun installable(apps: JSONArray?): List<App> {
        val out = ArrayList<App>()
        for (i in 0 until (apps?.length() ?: 0)) {
            val o = apps!!.optJSONObject(i) ?: continue
            if (!o.optBoolean("required", false)) continue
            val pkg = o.optString("package")
            val url = o.optString("url")
            val sha = o.optString("sha256").lowercase()
            val vc = o.optLong("version_code", -1)
            if (pkg.isNotEmpty() && url.startsWith("https://") && SHA.matches(sha) && vc > 0) {
                out += App(pkg, url, sha, vc)
            }
        }
        return out
    }

    /** האם צריך להתקין: לא מותקן, או מותקן בגרסה ישנה יותר. טהור. */
    fun needsInstall(app: App, installedVersion: Long?): Boolean =
        installedVersion == null || installedVersion < app.versionCode

    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "fp-managed").apply { isDaemon = true } }
    private val busy = AtomicBoolean(false)
    /** התקנות שנשלחו ועוד לא חזרה תוצאתן — חסימת ההתקנות חוזרת רק כשאין כאלה (אחרת השנייה תיכשל). */
    private val pending = ConcurrentHashMap.newKeySet<String>()
    private const val PREFS = "fp_managed"
    private const val K_RELAXED = "relaxed_install_apps"
    private const val K_RELAXED_AT = "relaxed_at"
    private const val RELAX_MAX_MS = 5 * 60_000L
    private const val K_STATE = "state_"            // + package → installing / installed / failed:<reason>

    /** פריט מהחנות (מהשרת) להתקנה עכשיו — בלי תלות ב"חובה". טהור. null אם חסר משהו להתקנה בטוחה. */
    fun fromStoreItem(o: JSONObject): App? {
        val pkg = o.optString("package")
        val url = o.optString("url")
        val sha = o.optString("sha256").lowercase()
        val vc = o.optLong("version_code", -1)
        return if (pkg.isNotEmpty() && url.startsWith("https://") && SHA.matches(sha) && vc > 0) App(pkg, url, sha, vc) else null
    }

    /**
     * התקנה / עדכון מהחנות, ביוזמת המשתמש (רק במנהל מכשיר — בלי "התקנה ממקור לא ידוע", ועובד גם
     * כשהתקנת אפליקציות חסומה בטלפון). מחזיר מיד: started / not_owner / busy / invalid / up_to_date.
     * המצב מתעדכן ב-state(); התוצאה הסופית מגיעה ל-UpdateReceiver.
     */
    fun installNow(context: Context, item: JSONObject): String {
        val app = context.applicationContext
        if (!DeviceOwnerManager.isDeviceOwner(app)) return "not_owner"
        val a = fromStoreItem(item) ?: return "invalid"
        if (!needsInstall(a, installedVersion(app, a.pkg))) return "up_to_date"
        if (!busy.compareAndSet(false, true)) return "busy"
        setState(app, a.pkg, "installing")
        worker.execute {
            try {
                val err = runCatching { install(app, a) }.getOrElse { "error_${it.javaClass.simpleName}".take(40) }
                if (err != null) setState(app, a.pkg, "failed:$err")
            } finally {
                done(app)
            }
        }
        return "started"
    }

    /** הסרה שקטה במנהל מכשיר (בלי חלון). מחזיר started / not_installed / error. בלי מנהל מכשיר — MainActivity פותח חלון. */
    fun uninstall(context: Context, pkg: String): String {
        val app = context.applicationContext
        if (installedVersion(app, pkg) == null) return "not_installed"
        if (!DeviceOwnerManager.isDeviceOwner(app)) return "needs_dialog"
        return runCatching {
            val intent = Intent(app, UpdateReceiver::class.java).setAction(ACTION_RESULT).putExtra(EXTRA_PACKAGE, pkg)
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0)
            val pi = PendingIntent.getBroadcast(app, ("u" + pkg).hashCode(), intent, flags)
            app.packageManager.packageInstaller.uninstall(pkg, pi.intentSender)
            "started"
        }.getOrElse { Log.w(TAG, "uninstall $pkg failed", it); "error" }
    }

    /** installing / installed / failed:<סיבה> / null — לממשק החנות. */
    fun state(context: Context, pkg: String): String? =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(K_STATE + pkg, null)

    private fun setState(context: Context, pkg: String, state: String?) {
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .apply { if (state == null) remove(K_STATE + pkg) else putString(K_STATE + pkg, state) }.apply()
    }

    /** תוצאת ההתקנה (UpdateReceiver): מחזירים את חסימת ההתקנות אם הורדה, ומעדכנים את המצב. */
    fun onResult(context: Context, pkg: String?, success: Boolean, message: String?) {
        pkg?.let { pending.remove(it) }
        if (!busy.get() && pending.isEmpty()) restoreRestrictions(context)
        if (pkg != null) setState(context, pkg, if (success) "installed" else "failed:${message.orEmpty().take(60)}")
        Log.i(TAG, "managed install result: $pkg ok=$success ${message.orEmpty().take(80)}")
    }

    /**
     * אם שחררנו את חסימת ההתקנות לזמן ההתקנה — מחזירים (בתוצאה, אחרי אתחול, או מהסנכרון התקופתי
     * אם התוצאה לא הגיעה תוך כמה דקות). staleOnly: רק אם השחרור ישן (לא באמצע התקנה).
     */
    fun restoreRestrictions(context: Context, staleOnly: Boolean = false) {
        val p = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (!p.getBoolean(K_RELAXED, false)) return
        if (staleOnly && (busy.get() || System.currentTimeMillis() - p.getLong(K_RELAXED_AT, 0) < RELAX_MAX_MS)) return
        p.edit().remove(K_RELAXED).remove(K_RELAXED_AT).commit()      // קודם — כדי ש-apply לא ידלג
        runCatching { DeviceOwnerManager.apply(context, GuardPolicy.get(context)) }
            .onFailure { Log.w(TAG, "restore install restriction failed", it) }
    }

    /** התקנה מהחנות באמצע (החסימה שוחררה לפני פחות מכמה דקות) — DeviceOwnerManager לא מחזיר אותה עכשיו. */
    fun installWindowOpen(context: Context): Boolean {
        val p = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return p.getBoolean(K_RELAXED, false) && System.currentTimeMillis() - p.getLong(K_RELAXED_AT, 0) < RELAX_MAX_MS
    }

    /** סוף עבודת הרקע: אם אין התקנה שממתינה לתוצאה (למשל ההורדה נכשלה) — מחזירים את החסימה כבר עכשיו. */
    private fun done(context: Context) {
        busy.set(false)
        if (pending.isEmpty()) restoreRestrictions(context)
    }

    /** אנדרואיד חוסם התקנות גם למנהל המכשיר כש-DISALLOW_INSTALL_APPS פעיל — משחררים לזמן ההתקנה בלבד. */
    private fun relaxInstallRestriction(context: Context) {
        if (context.getSystemService(UserManager::class.java)?.hasUserRestriction(UserManager.DISALLOW_INSTALL_APPS) != true) return
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(K_RELAXED, true)
            .putLong(K_RELAXED_AT, System.currentTimeMillis()).commit()
        context.getSystemService(DevicePolicyManager::class.java)
            ?.clearUserRestriction(DeviceAdmin.component(context), UserManager.DISALLOW_INSTALL_APPS)
    }

    /** נקרא אחרי heartbeat עם "managed_apps". מתקין אפליקציות חובה חסרות (רק במנהל מכשיר). */
    fun sync(context: Context, apps: JSONArray?) {
        val app = context.applicationContext
        if (!DeviceOwnerManager.isDeviceOwner(app)) return
        val todo = installable(apps).filter { needsInstall(it, installedVersion(app, it.pkg)) }
        if (todo.isEmpty() || !busy.compareAndSet(false, true)) return
        worker.execute {
            try {
                for (a in todo) {
                    runCatching { install(app, a) }.onFailure { Log.w(TAG, "managed install ${a.pkg} failed", it) }
                }
            } finally {
                done(app)
            }
        }
    }

    /** מוריד, בודק ומתקין. מחזיר קוד שגיאה, או null אם ההתקנה נשלחה (התוצאה תגיע ל-UpdateReceiver). */
    private fun install(context: Context, app: App): String? {
        val dir = File(context.noBackupFilesDir, "managed").apply { mkdirs() }
        val apk = File(dir, "${app.pkg}.apk")
        try {
            if (dir.usableSpace < 200L * 1024 * 1024) return "no_space"
            download(app, apk)?.let { Log.w(TAG, "managed ${app.pkg}: $it"); return it }
            // מוודאים שהקובץ הוא באמת החבילה הזו ובגרסה המבוקשת (בלי בדיקת חתימה — זו אפליקציית צד שלישי)
            @Suppress("DEPRECATION")
            val info = context.packageManager.getPackageArchiveInfo(apk.path, 0)
            val vc = info?.let { versionCode(it) } ?: -1
            if (info == null || info.packageName != app.pkg || vc != app.versionCode) {
                Log.w(TAG, "managed ${app.pkg}: archive mismatch"); return "archive_mismatch"
            }
            relaxInstallRestriction(context)
            try {
                val installer = context.packageManager.packageInstaller
                val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
                    setAppPackageName(app.pkg)
                    setSize(apk.length())
                }
                val id = installer.createSession(params)
                pending.add(app.pkg)
                installer.openSession(id).use { s ->
                    s.openWrite("base.apk", 0, apk.length()).use { out ->
                        apk.inputStream().use { it.copyTo(out, 1 shl 16) }
                        s.fsync(out)
                    }
                    val intent = Intent(context, UpdateReceiver::class.java).setAction(ACTION_RESULT)
                        .putExtra(EXTRA_PACKAGE, app.pkg)
                    val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                        (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0)
                    s.commit(PendingIntent.getBroadcast(context, app.pkg.hashCode(), intent, flags).intentSender)
                }
            } catch (e: Exception) {
                pending.remove(app.pkg)
                throw e
            }
            Log.i(TAG, "managed install committed: ${app.pkg} (${app.versionCode})")
            return null
        } finally {
            apk.delete()
        }
    }

    private fun download(app: App, dest: File): String? {
        var conn: HttpURLConnection? = null
        try {
            conn = (URL(app.url).openConnection() as HttpURLConnection).apply {
                connectTimeout = 30_000; readTimeout = 60_000; instanceFollowRedirects = false
            }
            if (conn.responseCode != HttpURLConnection.HTTP_OK) return "http_${conn.responseCode}"
            val digest = MessageDigest.getInstance("SHA-256")
            var total = 0L
            conn.inputStream.use { input ->
                dest.outputStream().use { out ->
                    val buf = ByteArray(1 shl 16)
                    while (true) {
                        val n = input.read(buf); if (n < 0) break
                        total += n; if (total > MAX_BYTES) return "too_large"
                        digest.update(buf, 0, n); out.write(buf, 0, n)
                    }
                }
            }
            val hex = digest.digest().joinToString("") { "%02x".format(it) }
            return if (hex == app.sha256) null else "sha256_mismatch"
        } catch (e: Exception) {
            return "download_${e.javaClass.simpleName}".take(40)
        } finally {
            conn?.disconnect()
        }
    }

    @Suppress("DEPRECATION")
    fun versionCode(info: android.content.pm.PackageInfo): Long =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) info.longVersionCode else info.versionCode.toLong()

    fun installedVersion(context: Context, pkg: String): Long? = try {
        versionCode(context.packageManager.getPackageInfo(pkg, 0))
    } catch (_: PackageManager.NameNotFoundException) {
        null
    }
}
