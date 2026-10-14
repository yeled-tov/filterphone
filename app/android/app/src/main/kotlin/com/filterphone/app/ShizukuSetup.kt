package com.filterphone.app

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import android.util.Log
import rikka.shizuku.Shizuku
import rikka.shizuku.ShizukuRemoteProcess
import java.io.InputStream
import java.lang.reflect.Method
import java.util.concurrent.TimeUnit

/**
 * הקמה בלחיצה אחת *בלי root* — דרך Shizuku (ניפוי אלחוטי / ADB רגיל).
 *
 * המשתמש מפעיל פעם אחת את אפליקציית Shizuku: ב"ניפוי אלחוטי" באותו מכשיר (אנדרואיד 11+, בלי
 * מחשב), או ממחשב פעם אחת ב-adb. Shizuku מריץ שרת מקומי בהרשאת shell (uid 2000) — בדיוק ההרשאה
 * של adb. מאז FilterPhone יכולה להריץ בלחיצה אחת את אותן פקודות ההקמה הרשמיות שטכנאי מריץ ב-adb:
 *
 *     dpm set-device-owner com.filterphone.app/.DeviceAdmin
 *     pm grant com.filterphone.app android.permission.WRITE_SECURE_SETTINGS
 *
 * אין כאן exploit ולא מעקף: אלו בדיוק הפקודות של [RootProvisioner], רק שהערוץ המורשה הוא Shizuku
 * (shell) במקום su (root). מה שאנדרואיד לא מתיר — מדווח ככשל, בדיוק כמו במסלול ה-root.
 *
 * הערה: Shizuku נותן הרשאת shell, לא root. היא מספיקה ל-dpm / pm grant (זה בדיוק מה ש-adb עושה),
 * אך לא לכתיבה ל-/system (למשל התקנת תעודה ברמת מערכת) — תעודה עדיין מותקנת כמנהל מכשיר או בחלון
 * האישור של המשתמש, בדיוק כמו בלי Shizuku.
 */
object ShizukuSetup {
    private const val TAG = "FilterPhone"
    private const val ADMIN = "com.filterphone.app/.DeviceAdmin"
    private const val PERM_REQUEST_CODE = 4210

    /** ה-binder של Shizuku חי (האפליקציה מותקנת ומופעלת). לא בודק הרשאה. */
    fun isAvailable(): Boolean = runCatching { Shizuku.pingBinder() }.getOrDefault(false)

    /** Shizuku חי *וגם* העניק לנו הרשאה — אפשר להריץ פקודות מורשות. */
    fun isReady(): Boolean = isAvailable() && hasPermission()

    /** האם המשתמש כבר אישר ל-FilterPhone להשתמש ב-Shizuku. */
    fun hasPermission(): Boolean = runCatching {
        // גרסאות Shizuku ישנות (pre-v11) דורשות זרימת הרשאה אחרת; מבקשים מהמשתמש לעדכן.
        !Shizuku.isPreV11() && Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED
    }.getOrDefault(false)

    /** גרסת שירות Shizuku (0 אם לא זמין) — לאבחון/דיווח בלבד. */
    fun version(): Int = runCatching { if (isAvailable()) Shizuku.getVersion() else 0 }.getOrDefault(0)

    /**
     * מבקש הרשאת Shizuku (חלון של אפליקציית Shizuku). אסינכרוני — יש להפעיל על חוט ה-UI.
     * קורא ל-[onResult] עם true/false כשההרשאה ניתנה/נדחתה (או מיד אם כבר יש הרשאה / אין Shizuku).
     */
    fun requestPermission(onResult: (Boolean) -> Unit) {
        if (!isAvailable()) { onResult(false); return }
        if (hasPermission()) { onResult(true); return }
        if (runCatching { Shizuku.isPreV11() }.getOrDefault(true)) { onResult(false); return }
        val listener = object : Shizuku.OnRequestPermissionResultListener {
            override fun onRequestPermissionResult(requestCode: Int, grantResult: Int) {
                if (requestCode != PERM_REQUEST_CODE) return
                Shizuku.removeRequestPermissionResultListener(this)
                onResult(grantResult == PackageManager.PERMISSION_GRANTED)
            }
        }
        Shizuku.addRequestPermissionResultListener(listener, Handler(Looper.getMainLooper()))
        runCatching { Shizuku.requestPermission(PERM_REQUEST_CODE) }.onFailure {
            Shizuku.removeRequestPermissionResultListener(listener)
            Log.w(TAG, "shizuku requestPermission failed", it)
            onResult(false)
        }
    }

    /**
     * ההקמה בפועל דרך Shizuku: הופך את FilterPhone למנהל המכשיר (Device Owner), ואז מעניק
     * WRITE_SECURE_SETTINGS. חוסם (I/O) — להריץ בחוט רקע. מחזיר [RootProvisioner.Outcome] זהה
     * במבנה למסלול ה-root (כולל מיפוי סיבות הכשל דרך [RootProvisioner.classifyDpmFailure]).
     */
    fun provision(context: Context): RootProvisioner.Outcome {
        if (DeviceOwnerManager.isDeviceOwner(context)) {
            return RootProvisioner.Outcome(true, "ENROLL-SHIZUKU-000", "already_managed")
        }
        if (!isReady()) {
            val why = if (!isAvailable()) "no_shizuku" else "shizuku_denied"
            return RootProvisioner.Outcome(false, "ENROLL-SHIZUKU-001", why, "Shizuku binder/permission unavailable")
        }
        // מריצים את הפקודה הרשמית; Android עצמו מאמת את מצב ההקמה ומחזיר את הסיבה האמיתית.
        val dpm = exec("dpm set-device-owner $ADMIN", 30)
        val combined = (dpm.out + "\n" + dpm.err).trim()
        if (DeviceOwnerManager.isDeviceOwner(context)) {
            val secure = exec("pm grant ${context.packageName} android.permission.WRITE_SECURE_SETTINGS", 20)
            val secureOut = (secure.out + "\n" + secure.err).trim()
            val granted = context.checkSelfPermission(Manifest.permission.WRITE_SECURE_SETTINGS) ==
                PackageManager.PERMISSION_GRANTED
            runCatching { DeviceOwnerManager.applyBaseline(context) }
                .onFailure { Log.w(TAG, "baseline after shizuku provision failed", it) }
            Recovery.scheduleDaily(context)
            val technical = listOfNotNull(
                combined.takeIf(String::isNotBlank),
                if (granted) "WRITE_SECURE_SETTINGS granted"
                else "WRITE_SECURE_SETTINGS grant failed: ${secureOut.take(200)}",
            ).joinToString("\n").take(300)
            return RootProvisioner.Outcome(true, "ENROLL-SHIZUKU-000", "device_owner_set", technical)
        }
        val reason = RootProvisioner.classifyDpmFailure(combined)
        Log.w(TAG, "shizuku provision failed: $reason")
        return RootProvisioner.Outcome(false, "ENROLL-SHIZUKU-003", reason, combined.take(300))
    }

    /** פקודת shell אחת דרך Shizuku (חוסם — חוט רקע). שקול ל-[RootProvisioner.runSu]. */
    internal fun exec(cmd: String, timeoutS: Long): RootProvisioner.Shell {
        return try {
            val proc = newProcess(arrayOf("sh", "-c", cmd))
            val out = StringBuilder()
            val err = StringBuilder()
            val tOut = drain(proc.inputStream, out)
            val tErr = drain(proc.errorStream, err)
            tOut.start()
            tErr.start()
            val finished = proc.waitForTimeout(timeoutS, TimeUnit.SECONDS)
            if (!finished) {
                proc.destroy()
                tOut.join(1_000)
                tErr.join(1_000)
                RootProvisioner.Shell(-2, snapshot(out), "timeout after ${timeoutS}s")
            } else {
                tOut.join(2_000)
                tErr.join(2_000)
                RootProvisioner.Shell(proc.exitValue(), snapshot(out), snapshot(err))
            }
        } catch (e: Exception) {
            RootProvisioner.Shell(-1, "", e.javaClass.simpleName + ": " + (e.message ?: ""))
        }
    }

    private fun snapshot(sb: StringBuilder): String = synchronized(sb) { sb.toString() }.trim()

    private fun drain(stream: InputStream, sink: StringBuilder): Thread = Thread({
        runCatching {
            stream.bufferedReader().useLines { lines ->
                lines.forEach { line ->
                    synchronized(sink) { if (sink.length < 4096) sink.append(line).append('\n') }
                }
            }
        }.onFailure { Log.w(TAG, "could not read shizuku output", it) }
    }, "filterphone-shizuku-output").apply { isDaemon = true }

    // Shizuku.newProcess הוא private ב-API; קוראים לו ברפלקשן. זו שיטה ציבורית-למעשה שכל אפליקציות
    // Shizuku משתמשות בה להרצת פקודות shell, ולא exploit. התוצאה היא Process רגיל.
    private val newProcessMethod: Method by lazy {
        Shizuku::class.java.getDeclaredMethod(
            "newProcess",
            Array<String>::class.java,
            Array<String>::class.java,
            String::class.java,
        ).apply { isAccessible = true }
    }

    private fun newProcess(cmd: Array<String>): ShizukuRemoteProcess =
        newProcessMethod.invoke(null, cmd, null, null) as ShizukuRemoteProcess
}
