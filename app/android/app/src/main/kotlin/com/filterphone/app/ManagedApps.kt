package com.filterphone.app

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import org.json.JSONArray
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
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

    /** נקרא אחרי heartbeat עם "managed_apps". מתקין אפליקציות חובה חסרות (רק במנהל מכשיר). */
    fun sync(context: Context, apps: JSONArray?) {
        val app = context.applicationContext
        if (!DeviceOwnerManager.isDeviceOwner(app)) return
        val todo = installable(apps).filter { needsInstall(it, installedVersion(app, it.pkg)) }
        if (todo.isEmpty() || !busy.compareAndSet(false, true)) return
        worker.execute {
            try {
                for (a in todo) runCatching { install(app, a) }.onFailure { Log.w(TAG, "managed install ${a.pkg} failed", it) }
            } finally {
                busy.set(false)
            }
        }
    }

    private fun install(context: Context, app: App) {
        val dir = File(context.noBackupFilesDir, "managed").apply { mkdirs() }
        val apk = File(dir, "${app.pkg}.apk")
        try {
            if (dir.usableSpace < 200L * 1024 * 1024) return
            download(app, apk)?.let { Log.w(TAG, "managed ${app.pkg}: $it"); return }
            // מוודאים שהקובץ הוא באמת החבילה הזו ובגרסה המבוקשת (בלי בדיקת חתימה — זו אפליקציית צד שלישי)
            @Suppress("DEPRECATION")
            val info = context.packageManager.getPackageArchiveInfo(apk.path, 0)
            val vc = info?.let { versionCode(it) } ?: -1
            if (info == null || info.packageName != app.pkg || vc != app.versionCode) {
                Log.w(TAG, "managed ${app.pkg}: archive mismatch"); return
            }
            val installer = context.packageManager.packageInstaller
            val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
                setAppPackageName(app.pkg)
                setSize(apk.length())
            }
            val id = installer.createSession(params)
            installer.openSession(id).use { s ->
                s.openWrite("base.apk", 0, apk.length()).use { out ->
                    apk.inputStream().use { it.copyTo(out, 1 shl 16) }
                    s.fsync(out)
                }
                val intent = Intent(context, UpdateReceiver::class.java).setAction(ACTION_RESULT)
                val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                    (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0)
                s.commit(PendingIntent.getBroadcast(context, app.pkg.hashCode(), intent, flags).intentSender)
            }
            Log.i(TAG, "managed install committed: ${app.pkg} (${app.versionCode})")
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
    private fun versionCode(info: android.content.pm.PackageInfo): Long =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) info.longVersionCode else info.versionCode.toLong()

    private fun installedVersion(context: Context, pkg: String): Long? = try {
        versionCode(context.packageManager.getPackageInfo(pkg, 0))
    } catch (_: PackageManager.NameNotFoundException) {
        null
    }
}
