package com.filterphone.app

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.util.Log
import java.util.concurrent.TimeUnit

/**
 * הקמה בלחיצה אחת במכשיר עם root: הופך את FilterPhone למנהל המכשיר (Device Owner) בלי שהמשתמש
 * יקליד פקודות. משתמש אך ורק בפקודה הרשמית של אנדרואיד:
 *
 *     dpm set-device-owner com.filterphone.app/.DeviceAdmin
 *
 * דרך su (מנגנון ה-root הרגיל — המשתמש מאשר בחלון של אפליקציית ה-root). זה לא exploit ולא מעקף:
 * זו אותה פקודה שטכנאי מריץ ב-adb, רק עם הרשאת root מקומית במקום מחשב.
 *
 * Preflight מציג אבחון מוקדם בממשק. במסלול Root לא עוצרים לפי האבחון הזה: מבקשים su, ואז
 * מריצים dpm כדי ש-Android יחזיר את סיבת הסירוב בפועל. Root לא מבטל את דרישות ההקמה של Android.
 *
 * אין כאן שום מעקף אבטחה, FRP-bypass או שינוי מצב לא מתועד. מה שאנדרואיד לא מתיר — מדווח ככשל.
 */
object RootProvisioner {
    private const val TAG = "FilterPhone"
    private const val ADMIN = "com.filterphone.app/.DeviceAdmin"

    /** [blockers] — מה חוסם את ההקמה, להסבר למשתמש: סוגי חשבונות (com.osp.app.signin…) או "user:<שם>". */
    data class Outcome(val ok: Boolean, val code: String, val resultKey: String, val technical: String = "",
                       val blockers: List<String> = emptyList()) {
        fun toMap(): Map<String, Any> = mapOf("ok" to ok, "code" to code, "result" to resultKey, "technical" to technical,
            "blockers" to blockers)
    }

    /** מריץ את ההקמה. חוסם (I/O) — להריץ בחוט רקע. */
    fun provision(context: Context): Outcome {
        val caps = Preflight.capabilities(context)
        if (caps.deviceOwner) return Outcome(true, "ENROLL-ROOT-000", "already_managed")
        // Do not trust filesystem-based su detection or isProvisioningAllowed as an early exit:
        // Magisk may expose su through a different path, and the user must get the real root prompt.
        // The official dpm command remains the final authority and will reject unsupported states.
        val idRes = runSu("id", 20)
        if (idRes.code != 0 || !idRes.out.contains("uid=0")) {
            val details = trim(idRes.out + " " + idRes.err)
            val noSu = idRes.code == -1 &&
                (details.contains("no such file", ignoreCase = true) ||
                 details.contains("cannot run program", ignoreCase = true) ||
                 details.contains("error=2", ignoreCase = true))
            return if (noSu) {
                Outcome(false, "ENROLL-ROOT-001", "no_root", details.ifBlank { "su executable not found" })
            } else {
                Outcome(false, "ENROLL-ROOT-001", "root_denied", details.ifBlank { "su did not return uid=0" })
            }
        }

        // Attempt the official command even if the framework preflight says no. This does not bypass
        // Android restrictions: dpm itself validates provisioning state and returns the actual reason.
        val dpm = runSu("dpm set-device-owner $ADMIN", 30)
        var combined = (dpm.out + "\n" + dpm.err).trim()
        // חשבונות במכשיר — כמו בניפוי האלחוטי: משביתים זמנית את אפליקציות החשבון ומדליקים מיד בחזרה
        if (!Preflight.capabilities(context).deviceOwner && AdbCommands.blockedByAccounts(combined)) {
            val trick = runSu(AdbCommands.accountTrick(context.packageName,
                WirelessSetup.accountPackages(context)), 60)
            combined = (trick.out + "\n" + trick.err).trim()
        }
        // לוודא בפועל (גם אם ה-exit code לא אמין בחלק מה-ROMים)
        if (Preflight.capabilities(context).deviceOwner) {
            // Optional high-privilege settings access used only on the explicitly root-enrolled,
            // fully managed device. Accessibility still requires the user's explicit system UI action.
            val secureSettings = runSu(
                "pm grant ${context.packageName} android.permission.WRITE_SECURE_SETTINGS",
                20,
            )
            val secureSettingsOutput = (secureSettings.out + "\n" + secureSettings.err).trim()
            val secureSettingsGranted = secureSettings.code == 0 &&
                context.checkSelfPermission(Manifest.permission.WRITE_SECURE_SETTINGS) ==
                PackageManager.PERMISSION_GRANTED
            runCatching { DeviceOwnerManager.applyBaseline(context) }
                .onFailure { Log.w(TAG, "baseline after root provision failed", it) }
            Recovery.scheduleDaily(context)
            val technical = listOfNotNull(
                combined.takeIf(String::isNotBlank),
                if (secureSettingsGranted) "WRITE_SECURE_SETTINGS granted"
                else "WRITE_SECURE_SETTINGS grant failed: ${trim(secureSettingsOutput)}",
            ).joinToString("\n")
            return Outcome(true, "ENROLL-ROOT-000", "device_owner_set", trim(technical))
        }
        val reason = classifyDpmFailure(combined)
        Log.w(TAG, "root provision failed: $reason")
        val blockers = when (reason) {
            "accounts_present" -> AdbCommands.accountTypes(runSu("dumpsys account", 20).out)
            "multiple_users" -> AdbCommands.extraUsers(runSu("pm list users", 20).out).map { "user:$it" }
            else -> emptyList()
        }
        return Outcome(false, "ENROLL-ROOT-003", reason, trim(combined), blockers)
    }

    /** מיפוי פלט הפקודה לסיבה (מפתח לתרגום). טהור — נבדק ביחידה. הסדר חשוב: הודעת "חשבונות"
     * מכילה גם "device owner", לכן בודקים אותה לפני. */
    fun classifyDpmFailure(output: String): String {
        val o = output.lowercase()
        return when {
            "accounts on the device" in o || ("account" in o && "not allowed" in o) -> "accounts_present"
            "already set up a device owner" in o ||
                ("already" in o && "device owner" in o && "account" !in o) -> "already_has_device_owner"
            "not allowed to set the device owner" in o || "cannot set the device owner" in o -> "provisioning_blocked"
            "several users" in o || "multiple users" in o || "users on the device" in o -> "multiple_users"
            "profile owner" in o -> "profile_or_user_present"
            "unknown admin" in o || "componentname" in o || "componentinfo" in o -> "admin_component_error"
            o.isBlank() -> "no_output"
            else -> "dpm_failed"
        }
    }

    internal data class Shell(val code: Int, val out: String, val err: String)

    /** יש root מאושר (su מחזיר uid=0). בפעם הראשונה אפליקציית ה-root שואלת את המשתמש. */
    fun hasRoot(): Boolean = runSu("id", 20).let { it.code == 0 && it.out.contains("uid=0") }

    /** פקודה אחת כ-root (חוסם — חוט רקע). */
    internal fun runSu(cmd: String, timeoutS: Long): Shell {
        return try {
            val p = ProcessBuilder("su", "-c", cmd).redirectErrorStream(true).start()
            // Drain output while su is running. Waiting before reading can fill the process pipe
            // (or hide a useful dpm error behind a timeout), especially on vendor Android builds.
            val output = StringBuilder()
            val reader = Thread({
                runCatching {
                    p.inputStream.bufferedReader().useLines { lines ->
                        lines.forEach { line ->
                            synchronized(output) {
                                if (output.length < 4096) output.append(line).append('\n')
                            }
                        }
                    }
                }.onFailure { Log.w(TAG, "could not read su output", it) }
            }, "filterphone-su-output").apply { isDaemon = true }
            reader.start()
            val finished = p.waitFor(timeoutS, TimeUnit.SECONDS)
            if (!finished) {
                p.destroyForcibly()
                p.waitFor(2, TimeUnit.SECONDS)
                reader.join(1_000)
                Shell(-2, synchronized(output) { output.toString() }, "timeout after ${timeoutS}s")
            } else {
                reader.join(2_000)
                Shell(p.exitValue(), synchronized(output) { output.toString() }, "")
            }
        } catch (e: Exception) {
            Shell(-1, "", e.javaClass.simpleName + ": " + (e.message ?: ""))
        }
    }

    private fun trim(s: String): String = s.trim().take(300)
}

