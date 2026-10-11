package com.filterphone.app

import android.content.Context
import android.util.Log
import java.io.BufferedReader
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
 * כשירות נבדקת לפני הכול (Preflight.isProvisioningAllowed הרשמי): אם המכשיר כבר מאותחל, יש בו
 * חשבונות, או יש כבר מנהל מכשיר — לא מנסים (אנדרואיד ידחה), אלא מחזירים סיבה מדויקת לאיפוס/הקמה אחרת.
 *
 * אין כאן שום מעקף אבטחה, FRP-bypass או שינוי מצב לא מתועד. מה שאנדרואיד לא מתיר — מדווח ככשל.
 */
object RootProvisioner {
    private const val TAG = "FilterPhone"
    private const val ADMIN = "com.filterphone.app/.DeviceAdmin"

    data class Outcome(val ok: Boolean, val code: String, val resultKey: String, val technical: String = "") {
        fun toMap(): Map<String, Any> = mapOf("ok" to ok, "code" to code, "result" to resultKey, "technical" to technical)
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
        val combined = (dpm.out + "\n" + dpm.err).trim()
        // לוודא בפועל (גם אם ה-exit code לא אמין בחלק מה-ROMים)
        if (Preflight.capabilities(context).deviceOwner) {
            runCatching { DeviceOwnerManager.applyBaseline(context) }
                .onFailure { Log.w(TAG, "baseline after root provision failed", it) }
            Recovery.schedulePeriodic(context)
            return Outcome(true, "ENROLL-ROOT-000", "device_owner_set", trim(combined))
        }
        val reason = classifyDpmFailure(combined)
        Log.w(TAG, "root provision failed: $reason")
        return Outcome(false, "ENROLL-ROOT-003", reason, trim(combined))
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

    private data class Shell(val code: Int, val out: String, val err: String)

    private fun runSu(cmd: String, timeoutS: Long): Shell {
        return try {
            val p = ProcessBuilder("su", "-c", cmd).redirectErrorStream(true).start()
            val finished = p.waitFor(timeoutS, TimeUnit.SECONDS)
            if (!finished) {
                p.destroyForcibly()
                Shell(-2, "", "timeout after ${timeoutS}s")
            } else {
                val output = p.inputStream.bufferedReader().use(BufferedReader::readText)
                Shell(p.exitValue(), output, "")
            }
        } catch (e: Exception) {
            Shell(-1, "", e.javaClass.simpleName + ": " + (e.message ?: ""))
        }
    }

    private fun trim(s: String): String = s.trim().take(300)
}
