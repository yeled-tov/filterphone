package com.filterphone.app

import android.content.Context
import android.util.Log
import java.io.IOException
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

/**
 * הקמה דרך הפקודה הרשמית dpm set-device-owner. root מתבקש בפועל דרך su;
 * אין חסימה מוקדמת לפי isProvisioningAllowed, כי הפקודה עצמה מחזירה את סיבת הסירוב המדויקת.
 * אין הסרה אוטומטית של חשבונות, איפוס, או מעקף FRP.
 */
object RootProvisioner {
    private const val TAG = "FilterPhone"
    private const val ADMIN = "com.filterphone.app/.DeviceAdmin"

    data class Outcome(
        val ok: Boolean,
        val code: String,
        val resultKey: String,
        val technical: String = "",
        val accounts: List<String> = emptyList(),
    ) {
        fun toMap(): Map<String, Any> = mapOf(
            "ok" to ok, "code" to code, "result" to resultKey,
            "technical" to technical, "accounts" to accounts,
        )
    }

    fun provision(context: Context): Outcome {
        val before = Preflight.capabilities(context)
        if (before.deviceOwner) return Outcome(true, "ENROLL-ROOT-000", "already_managed")

        // אין לבדוק קיום קובץ su או provisioningAllowed לפני ההרצה:
        // זה מנע בחלק מהמכשירים את הופעת בקשת root עצמה.
        val idRes = runSu("id", 20)
        if (idRes.code != 0 || !idRes.out.contains("uid=0")) {
            val combined = trim(idRes.out + "\n" + idRes.err)
            val absent = idRes.err.contains("error=2", true) ||
                idRes.err.contains("not found", true) ||
                idRes.err.contains("no such file", true)
            return Outcome(false, "ENROLL-ROOT-001",
                if (absent) "no_root" else "root_denied", combined)
        }

        // אחרי שהמשתמש אישר root אפשר להציג שמות/סוגים ש-dumpsys מדווח.
        val accountDump = runSu("dumpsys account", 12)
        val accounts = parseAccounts(accountDump.out + "\n" + accountDump.err)
        val dpm = runSu("dpm set-device-owner $ADMIN", 30)
        val combined = trim(dpm.out + "\n" + dpm.err)

        if (Preflight.capabilities(context).deviceOwner) {
            runCatching { DeviceOwnerManager.applyBaseline(context) }
                .onFailure { Log.w(TAG, "baseline after root provision failed", it) }
            Recovery.schedulePeriodic(context)
            return Outcome(true, "ENROLL-ROOT-000", "device_owner_set", combined, accounts)
        }

        val reason = classifyDpmFailure(combined)
        val finalReason = if (reason == "provisioning_blocked" && accounts.isNotEmpty()) {
            "accounts_present"
        } else reason
        Log.w(TAG, "root provision failed: $finalReason")
        return Outcome(false, "ENROLL-ROOT-003", finalReason, combined, accounts)
    }

    /** מיפוי פלט DPM לסיבה שאפשר להציג ב-UI ולבדוק ביחידה. */
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

    /** מחזיר רק שם וסוג חשבון; לעולם לא סיסמאות או אסימונים. */
    fun parseAccounts(output: String): List<String> {
        val pattern = Regex("Account \\{name=([^,}]+),\\s*type=([^,}]+)\\}", RegexOption.IGNORE_CASE)
        return pattern.findAll(output).map { match ->
            val name = match.groupValues[1].trim().take(120)
            val type = match.groupValues[2].trim().take(80)
            "$name ($type)"
        }.distinct().take(20).toList()
    }

    private data class Shell(val code: Int, val out: String, val err: String)

    /** קורא stdout/stderr במקביל כדי ש-readText לא יעקוף את מגבלת הזמן. */
    private fun runSu(cmd: String, timeoutS: Long): Shell {
        var process: Process? = null
        return try {
            process = ProcessBuilder("su", "-c", cmd).redirectErrorStream(false).start()
            val p = process
            val outFuture = CompletableFuture.supplyAsync {
                p.inputStream.bufferedReader().use { it.readText() }
            }
            val errFuture = CompletableFuture.supplyAsync {
                p.errorStream.bufferedReader().use { it.readText() }
            }
            val finished = p.waitFor(timeoutS, TimeUnit.SECONDS)
            if (!finished) {
                p.destroyForcibly()
                Shell(-2, readQuietly(outFuture), "timeout אחרי ${timeoutS} שניות")
            } else {
                Shell(p.exitValue(), readQuietly(outFuture), readQuietly(errFuture))
            }
        } catch (e: Exception) {
            process?.destroyForcibly()
            val absent = e is IOException && (e.message?.contains("error=2") == true ||
                e.message?.contains("No such file", true) == true)
            Shell(-1, "", if (absent) "su אינו מותקן" else e.javaClass.simpleName + ": " + (e.message ?: ""))
        }
    }

    private fun readQuietly(future: CompletableFuture<String>): String =
        runCatching { future.get(2, TimeUnit.SECONDS) }.getOrDefault("")

    private fun trim(s: String): String = s.trim().take(1200)
}