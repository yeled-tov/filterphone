package com.filterphone.app

import android.app.PendingIntent
import android.app.admin.DevicePolicyManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.os.Build
import android.os.UserManager
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * עדכון האפליקציה עצמה.
 *
 * מנהל מכשיר (Device Owner): במצב הזה התקנת קובצי APK חסומה בטלפון (disallow_unknown_sources),
 * ולכן גם FilterPhone לא הייתה מתעדכנת. אנדרואיד מתיר ל-Device Owner להתקין בשקט דרך
 * PackageInstaller (API רשמי) — וזה מה שנעשה כאן, בזהירות:
 *   1. רק גרסה חדשה יותר (versionCode) שמתאימה למעבד של הטלפון;
 *   2. הורדה ב-HTTPS בלבד, עם גבול גודל ובדיקת מקום פנוי; ברשת לא מוגבלת (Wi-Fi), או אחרי
 *      3 ימי המתנה — כדי לא לבזבז חבילת גלישה, אבל גם לא להישאר מאחור לתמיד;
 *   3. SHA-256 של הקובץ = מה שהשרת פרסם (והשרת בדק אותו מול הקובץ שנבנה);
 *   4. בקובץ עצמו: אותה חבילה, אותו versionCode, ואותה תעודת חתימה כמו המותקנת. (אנדרואיד עצמו
 *      מסרב לעדכון בחתימה אחרת — זו שכבה נוספת, עם שגיאה ברורה.) אין "שדרוג לאחור";
 *   5. ההתקנה. אם המנהל חסם התקנת אפליקציות (DISALLOW_INSTALL_APPS — אנדרואיד חוסם בה גם את
 *      מנהל המכשיר), החסימה משוחררת רק לזמן ההתקנה ומוחזרת בתוצאה; וגם DeviceOwnerManager.apply
 *      (אחרי העדכון ובמשימה התקופתית) מחזיר אותה בכל מקרה.
 *   כל כישלון: לא מתקינים, מדווחים לשרת (heartbeat → "update"), ומנסים שוב בהשהיה הולכת וגדלה.
 *
 * בלי מנהל מכשיר אנדרואיד לא מתיר עדכון שקט: רק נשמר שיש גרסה חדשה, והאפליקציה מציגה הודעה
 * עם קישור לדף ההורדה — המשתמש מתקין בעצמו. באנדרואיד 16 — במקום הדפדפן, האפליקציה מורידה ובודקת
 * את הקובץ בעצמה ומבקשת מאנדרואיד להתקין (המשתמש מאשר בחלון של אנדרואיד), כדי שתוכל להוריד את
 * ה-VPN רגע לפני ההתקנה (באג של אנדרואיד 16 — TunnelManager.pauseForUpdate).
 */
object SelfUpdate {
    private const val TAG = "FilterPhone"
    private const val PREFS = "fp_update"
    private const val K_STATE = "state"            // available / waiting / installing / failed
    private const val K_VERSION = "version"        // versionName של הגרסה המוצעת
    private const val K_CODE = "code"
    private const val K_ERROR = "error"
    private const val K_PAGE = "page"              // דף ההורדה — להודעה כשאין מנהל מכשיר
    private const val K_URL = "url"                // הקובץ המוצע — להתקנה מתוך האפליקציה (אנדרואיד 16)
    private const val K_SHA = "sha256"
    private const val K_SIZE = "size"
    private const val K_FIRST_SEEN = "first_seen"
    private const val K_STATE_AT = "state_at"
    private const val K_RELAXED = "relaxed_install_apps"
    private const val K_RELAXED_AT = "relaxed_at"
    const val ACTION_RESULT = "com.filterphone.app.UPDATE_RESULT"
    const val ACTION_RESULT_USER = "com.filterphone.app.UPDATE_RESULT_USER"   // התקנה שהמשתמש מאשר
    const val MAX_BYTES = 200L * 1024 * 1024
    private const val METERED_AFTER_MS = 3L * 24 * 3600 * 1000
    private const val INSTALL_STALE_MS = 30L * 60 * 1000
    private const val INSTALL_WINDOW_MS = 5L * 60 * 1000
    private val STEPS = longArrayOf(3_600_000, 3 * 3_600_000, 6 * 3_600_000, 12 * 3_600_000, 24 * 3_600_000)
    private val SHA_HEX = Regex("^[0-9a-f]{64}$")

    data class Candidate(
        val url: String,
        val sha256: String,
        val versionCode: Long,
        val versionName: String,
        val size: Long,
    )

    /** בוחר קובץ: חדש מ-[installed], מתאים לאחד המעבדים [abis], ונתונים תקינים. הכי חדש מנצח. */
    fun pick(update: JSONObject?, installed: Long, abis: List<String>): Candidate? {
        val files = update?.optJSONArray("files") ?: return null
        val name = update.optString("version_name").take(20)
        var best: Candidate? = null
        for (i in 0 until files.length()) {
            val f = files.optJSONObject(i) ?: continue
            val code = f.optLong("version_code", -1)
            val url = f.optString("url")
            val sha = f.optString("sha256").lowercase()
            val size = f.optLong("size", -1)
            if (code <= installed || !url.startsWith("https://") || !SHA_HEX.matches(sha) || size !in 1..MAX_BYTES) continue
            val fileAbis = f.optJSONArray("abis")?.let { a -> (0 until a.length()).map { a.optString(it) } }.orEmpty()
            if (fileAbis.isNotEmpty() && fileAbis.none { it in abis }) continue
            if (best == null || code > best.versionCode) best = Candidate(url, sha, code, name, size)
        }
        return best
    }

    /** האם תעודות החתימה של הקובץ החדש ממשיכות את המותקנות (זהות, או רוטציה שכוללת את המותקנת). */
    fun sameSigner(installed: Set<String>, archiveCurrent: Set<String>, archiveHistory: Set<String>): Boolean =
        installed.isNotEmpty() && (archiveCurrent == installed || installed.all { it in archiveHistory })

    private fun prefs(context: Context): SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun backoff(context: Context) = Backoff(prefs(context), "self_update", STEPS)

    /** הורדה והתקנה — בחוט משלהן, כדי לא לעכב את הדיווח לשרת ואת המסך. אחת בכל פעם. */
    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "fp-update").apply { isDaemon = true } }
    private val busy = AtomicBoolean(false)

    /** נקרא אחרי כל heartbeat עם "app_update" מהשרת (או null — אין גרסה שפורסמה). */
    fun consider(context: Context, update: JSONObject?) {
        val app = context.applicationContext
        val p = prefs(app)
        val c = pick(update, installedVersionCode(app), Build.SUPPORTED_ABIS.toList())
        if (c == null) {
            // מעודכן (או שאין קובץ מתאים). אחרי עדכון מוצלח — מחזירים חסימות ומנקים
            val updated = installedVersionCode(app) >= p.getLong(K_CODE, Long.MAX_VALUE)
            if (updated || p.getString(K_STATE, null) != "installing" || installStale(p)) {
                restoreRestrictions(app)
                clear(app)
            }
            return
        }
        if (c.versionCode != p.getLong(K_CODE, -1)) {
            p.edit().clear().putLong(K_CODE, c.versionCode).putString(K_VERSION, c.versionName)
                .putLong(K_FIRST_SEEN, System.currentTimeMillis()).apply()     // גרסה חדשה — השהיות מההתחלה
        }
        if (!DeviceOwnerManager.isDeviceOwner(app)) {
            p.edit().putString(K_PAGE, c.url.substringBeforeLast('/') + "/")
                .putString(K_URL, c.url).putString(K_SHA, c.sha256).putLong(K_SIZE, c.size).apply()
            if (p.getString(K_STATE, null) != "installing" || installStale(p)) setState(app, "available", null)
            return
        }
        if (p.getString(K_STATE, null) == "installing" && !installStale(p)) return
        val b = backoff(app)
        if (!b.ready() || !busy.compareAndSet(false, true)) return
        val metered = runCatching { app.getSystemService(ConnectivityManager::class.java)?.isActiveNetworkMetered }
            .getOrNull() ?: true
        if (metered && System.currentTimeMillis() - p.getLong(K_FIRST_SEEN, 0L) < METERED_AFTER_MS) {
            busy.set(false)
            setState(app, "waiting", null)
            return
        }
        b.failed()          // הניסיון "נכשל" עד שיוכח אחרת: גם אם התהליך נהרג באמצע — לא מנסים שוב מיד
        worker.execute {
            try {
                val error = try {
                    install(app, c)
                } catch (e: Exception) {
                    Log.w(TAG, "self-update failed", e)
                    e.javaClass.simpleName
                }
                if (error != null) setState(app, "failed", error.take(40))
            } finally {
                busy.set(false)
            }
        }
    }

    /** מוריד ובודק את הקובץ. (הקובץ, null) — או (null, קוד שגיאה). */
    private fun fetch(context: Context, c: Candidate): Pair<File?, String?> {
        val dir = File(context.noBackupFilesDir, "update").apply { mkdirs() }
        dir.listFiles()?.forEach { it.delete() }
        if (dir.usableSpace < c.size * 2 + 50L * 1024 * 1024) return null to "no_space"
        val apk = File(dir, "FilterPhone.apk")
        setState(context, "installing", null)
        download(c, apk)?.let { apk.delete(); return null to it }
        verifyArchive(context, apk, c)?.let { apk.delete(); return null to it }
        return apk to null
    }

    /** כותב את הקובץ ל-PackageInstaller ושולח. התוצאה תגיע ל-UpdateReceiver עם [action]. */
    private fun commit(context: Context, apk: File, c: Candidate, action: String, requestCode: Int) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            setSize(apk.length())
        }
        val id = installer.createSession(params)
        installer.openSession(id).use { session ->
            session.openWrite("base.apk", 0, apk.length()).use { out ->
                apk.inputStream().use { it.copyTo(out, 1 shl 16) }
                session.fsync(out)
            }
            val intent = Intent(context, UpdateReceiver::class.java).setAction(action)
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0)
            // אנדרואיד 16 (מנהל מכשיר — התקנה שקטה מיד): ה-VPN יורד ממש לפני ההתקנה
            if (action == ACTION_RESULT) TunnelManager.pauseForUpdate()
            session.commit(PendingIntent.getBroadcast(context, requestCode, intent, flags).intentSender)
        }
        Log.i(TAG, "self-update: committed ${c.versionName} (${c.versionCode})")
    }

    /** מנהל מכשיר: מוריד, בודק ומתקין בשקט. קוד שגיאה, או null אם ההתקנה נשלחה. */
    private fun install(context: Context, c: Candidate): String? {
        val (apk, error) = fetch(context, c)
        if (apk == null) return error
        try {
            if (context.getSystemService(UserManager::class.java)?.hasUserRestriction(UserManager.DISALLOW_INSTALL_APPS) == true) {
                prefs(context).edit().putBoolean(K_RELAXED, true).putLong(K_RELAXED_AT, System.currentTimeMillis()).commit()
                context.getSystemService(DevicePolicyManager::class.java)
                    ?.clearUserRestriction(DeviceAdmin.component(context), UserManager.DISALLOW_INSTALL_APPS)
            }
            commit(context, apk, c, ACTION_RESULT, 4301)
            return null
        } catch (e: Exception) {
            restoreRestrictions(context)
            if (TunnelManager.endUpdatePause()) TunnelManager.ensureConnected("update-failed")
            throw e
        }
    }

    /** בלי מנהל מכשיר, באנדרואיד 16: מתקינים מתוך האפליקציה (ולא מהדפדפן) — כדי להוריד את ה-VPN לפני. */
    fun installInApp(context: Context): Boolean =
        TunnelManager.updateBugAffected && !DeviceOwnerManager.isDeviceOwner(context)

    /**
     * עדכון שהמשתמש ביקש (בלי מנהל מכשיר): מוריד ובודק, ושולח ל-PackageInstaller. אנדרואיד יבקש אישור
     * (STATUS_PENDING_USER_ACTION → confirm). רץ ברקע. קוד שגיאה, או null אם נשלח.
     */
    fun installRequested(context: Context): String? {
        val app = context.applicationContext
        val p = prefs(app)
        val c = Candidate(
            url = p.getString(K_URL, null) ?: return "no_update",
            sha256 = p.getString(K_SHA, null) ?: return "no_update",
            versionCode = p.getLong(K_CODE, -1L).takeIf { it > 0 } ?: return "no_update",
            versionName = p.getString(K_VERSION, null) ?: "",
            size = p.getLong(K_SIZE, -1L).takeIf { it in 1..MAX_BYTES } ?: return "no_update",
        )
        if (!busy.compareAndSet(false, true)) return "busy"
        return try {
            val (apk, error) = fetch(app, c)
            if (apk == null) {
                setState(app, "available", error)
                error
            } else {
                commit(app, apk, c, ACTION_RESULT_USER, 4302)
                null
            }
        } catch (e: Exception) {
            Log.w(TAG, "self-update (user) failed", e)
            setState(app, "available", e.javaClass.simpleName.take(40))
            "install_${e.javaClass.simpleName}".take(40)
        } finally {
            busy.set(false)
        }
    }

    /**
     * אנדרואיד מבקש מהמשתמש לאשר את ההתקנה. באנדרואיד 16 ה-VPN יורד עכשיו — מיד אחרי האישור ההתקנה
     * רצה, ולא מקבלים הזדמנות נוספת. ביטול / כישלון — onResult מחבר מחדש (ולכל היותר אחרי 2 דקות).
     */
    fun confirmRequested(context: Context, confirm: Intent?) {
        if (confirm == null) {
            onResult(context, PackageInstaller.STATUS_FAILURE, "no confirm intent", userInitiated = true)
            return
        }
        TunnelManager.pauseForUpdate()
        try {
            context.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: Exception) {
            Log.w(TAG, "install confirmation did not open", e)
            onResult(context, PackageInstaller.STATUS_FAILURE, "confirm: ${e.javaClass.simpleName}", userInitiated = true)
        }
    }

    private fun download(c: Candidate, dest: File): String? {
        var conn: HttpURLConnection? = null
        try {
            conn = (URL(c.url).openConnection() as HttpURLConnection).apply {
                connectTimeout = 30_000
                readTimeout = 60_000
                instanceFollowRedirects = false
            }
            if (conn.responseCode != HttpURLConnection.HTTP_OK) return "http_${conn.responseCode}"
            val length = conn.contentLengthLong
            if (length > 0 && length != c.size) return "size_mismatch"
            val digest = MessageDigest.getInstance("SHA-256")
            var total = 0L
            conn.inputStream.use { input ->
                dest.outputStream().use { out ->
                    val buf = ByteArray(1 shl 16)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        total += n
                        if (total > c.size) return "size_mismatch"
                        digest.update(buf, 0, n)
                        out.write(buf, 0, n)
                    }
                }
            }
            if (total != c.size) return "size_mismatch"
            val hex = digest.digest().joinToString("") { "%02x".format(it) }
            return if (hex == c.sha256) null else "sha256_mismatch"
        } catch (e: Exception) {
            return "download_${e.javaClass.simpleName}".take(40)
        } finally {
            conn?.disconnect()
        }
    }

    private fun verifyArchive(context: Context, apk: File, c: Candidate): String? {
        val pm = context.packageManager
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) PackageManager.GET_SIGNING_CERTIFICATES
        else @Suppress("DEPRECATION") PackageManager.GET_SIGNATURES
        @Suppress("DEPRECATION")
        val archive = pm.getPackageArchiveInfo(apk.path, flags) ?: return "unreadable_apk"
        if (archive.packageName != context.packageName) return "wrong_package"
        if (versionCode(archive) != c.versionCode) return "version_mismatch"
        if (versionCode(archive) <= installedVersionCode(context)) return "not_newer"
        @Suppress("DEPRECATION")
        val installed = pm.getPackageInfo(context.packageName, flags)
        val mine = signers(installed).first
        val (current, history) = signers(archive)
        // אם אנדרואיד לא החזיר תעודות מהקובץ — הוא עדיין אוכף חתימה זהה בהתקנה עצמה
        if (current.isNotEmpty() && !sameSigner(mine, current, history)) return "signature_mismatch"
        return null
    }

    /** (התעודות הנוכחיות, ההיסטוריה שלהן) — SHA-256 של כל תעודה. */
    private fun signers(info: PackageInfo): Pair<Set<String>, Set<String>> {
        fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            val s = info.signingInfo ?: return emptySet<String>() to emptySet()
            // חותם אחד: ההיסטוריה מסודרת מהתעודה המקורית (0) לנוכחית (האחרונה)
            val current = if (s.hasMultipleSigners()) s.apkContentsSigners.orEmpty().toList()
            else s.signingCertificateHistory.orEmpty().takeLast(1)
            val history = s.signingCertificateHistory.orEmpty().map { sha(it.toByteArray()) }.toSet()
            return current.map { sha(it.toByteArray()) }.toSet() to history
        }
        @Suppress("DEPRECATION")
        val sigs = info.signatures.orEmpty().map { sha(it.toByteArray()) }.toSet()
        return sigs to sigs
    }

    @Suppress("DEPRECATION")
    private fun versionCode(info: PackageInfo): Long =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) info.longVersionCode else info.versionCode.toLong()

    fun installedVersionCode(context: Context): Long =
        runCatching { versionCode(context.packageManager.getPackageInfo(context.packageName, 0)) }.getOrDefault(0L)

    /** תוצאת ההתקנה (מ-UpdateReceiver). בהצלחה התהליך כבר רץ בגרסה החדשה. רץ ברקע. */
    fun onResult(context: Context, status: Int, message: String?, userInitiated: Boolean = false) {
        restoreRestrictions(context)
        File(context.noBackupFilesDir, "update").listFiles()?.forEach { it.delete() }
        when (status) {
            PackageInstaller.STATUS_SUCCESS -> {
                Log.i(TAG, "self-update: installed")
                clear(context)
            }
            // למנהל מכשיר זה לא אמור לקרות; לא פותחים חלון מהרקע — מדווחים
            PackageInstaller.STATUS_PENDING_USER_ACTION -> setState(context, "failed", "needs_user_action")
            else -> {
                Log.w(TAG, "self-update: status $status ${message.orEmpty().take(120)}")
                // המשתמש ביטל / נכשל — ההודעה "יש גרסה חדשה" נשארת, כדי שאפשר לנסות שוב
                setState(context, if (userInitiated) "available" else "failed", "install_status_$status")
            }
        }
        // ה-VPN הורד לקראת ההתקנה (אנדרואיד 16) — מחברים מחדש מיד
        if (TunnelManager.endUpdatePause()) TunnelManager.ensureConnected("update-result")
        Recovery.syncSoon(context)
    }

    /** אם החסימה שוחררה לזמן ההתקנה — מחזירים אותה (DeviceOwnerManager.apply מחזיר הכול בכל מקרה). */
    fun restoreRestrictions(context: Context) {
        val p = prefs(context)
        if (!p.getBoolean(K_RELAXED, false)) return
        p.edit().remove(K_RELAXED).remove(K_RELAXED_AT).commit()      // קודם — כדי ש-apply לא ידלג
        runCatching { DeviceOwnerManager.apply(context, GuardPolicy.get(context)) }
            .onFailure { Log.w(TAG, "restore restrictions failed", it) }
    }

    /** ההתקנה שחררה עכשיו את חסימת ההתקנות (לכמה דקות לכל היותר) — DeviceOwnerManager לא מחזיר אותה באמצע. */
    fun installWindowOpen(context: Context): Boolean {
        val p = prefs(context)
        return p.getBoolean(K_RELAXED, false) &&
            System.currentTimeMillis() - p.getLong(K_RELAXED_AT, 0L) < INSTALL_WINDOW_MS
    }

    private fun setState(context: Context, state: String, error: String?) {
        prefs(context).edit().putString(K_STATE, state).putLong(K_STATE_AT, System.currentTimeMillis())
            .apply { if (error == null) remove(K_ERROR) else putString(K_ERROR, error) }.apply()
    }

    private fun installStale(p: SharedPreferences) = System.currentTimeMillis() - p.getLong(K_STATE_AT, 0L) > INSTALL_STALE_MS

    private fun clear(context: Context) {
        val p = prefs(context)
        val relaxed = p.getBoolean(K_RELAXED, false)
        p.edit().clear().apply { if (relaxed) putBoolean(K_RELAXED, true) }.apply()
    }

    /** לדיווח לשרת: {state, version, error} — או null כשאין מה לדווח. */
    fun report(context: Context): JSONObject? {
        val p = prefs(context)
        val state = p.getString(K_STATE, null) ?: return null
        return JSONObject().put("state", state)
            .apply { p.getString(K_VERSION, null)?.let { put("version", it) } }
            .apply { p.getString(K_ERROR, null)?.let { put("error", it) } }
    }

    /** למסך הראשי: גרסה חדשה שהמשתמש צריך להתקין בעצמו (בלי מנהל מכשיר). */
    fun available(context: Context): Map<String, String>? {
        val p = prefs(context)
        if (p.getString(K_STATE, null) != "available") return null
        // כבר עודכן (התקנה ידנית מדף ההורדה) — לא מחכים לתשובה מהשרת כדי להוריד את ההודעה
        if (installedVersionCode(context) >= p.getLong(K_CODE, Long.MAX_VALUE)) {
            clear(context)
            return null
        }
        val page = p.getString(K_PAGE, null) ?: return null
        return mapOf("version" to (p.getString(K_VERSION, null) ?: ""), "page" to page)
    }
}

/** תוצאת ההתקנה מ-PackageInstaller (לא חשוף לאפליקציות אחרות — רק ה-PendingIntent שלנו). */
class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
        when (intent.action) {
            SelfUpdate.ACTION_RESULT, SelfUpdate.ACTION_RESULT_USER -> {
                val user = intent.action == SelfUpdate.ACTION_RESULT_USER
                val confirm: Intent? = if (user && status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
                    if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                    else @Suppress("DEPRECATION") intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                } else null
                val pending = goAsync()
                Recovery.runAsync(context) {
                    try {
                        if (user && status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
                            SelfUpdate.confirmRequested(context.applicationContext, confirm)
                        } else {
                            SelfUpdate.onResult(context.applicationContext, status, message, userInitiated = user)
                        }
                    } finally {
                        pending.finish()
                    }
                }
            }
            // התקנה מהחנות / מהקטלוג: מחזירים את חסימת ההתקנות ומעדכנים את המצב
            ManagedApps.ACTION_RESULT -> {
                if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) return   // לא קורה במנהל מכשיר
                ManagedApps.onResult(context.applicationContext, intent.getStringExtra(ManagedApps.EXTRA_PACKAGE),
                    status == PackageInstaller.STATUS_SUCCESS, message)
            }
        }
    }
}
