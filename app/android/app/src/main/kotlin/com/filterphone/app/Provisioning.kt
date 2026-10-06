package com.filterphone.app

import android.app.Activity
import android.app.admin.DevicePolicyManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.PersistableBundle
import android.util.Log

/**
 * הקמת טלפון מאופס כ-Device Owner (Android Enterprise, "fully managed") — בסריקת QR מממשק הניהול.
 *
 * הזרימה החדשה (חובה מאנדרואיד 12; בחלק מהמכשירים כבר מ-10):
 *   1. המערכת מורידה את ה-APK, בודקת את הטביעה, ומפעילה את GET_PROVISIONING_MODE →
 *      ProvisioningModeActivity עונה "מכשיר מנוהל במלואו".
 *   2. בסוף ההקמה: ADMIN_POLICY_COMPLIANCE → PolicyComplianceActivity מחילה את הגדרות הבסיס
 *      ושומרת את קוד ההפעלה החד-פעמי שבתוך ה-QR (fp_code) — האפליקציה מתחברת איתו לבד.
 * בזרימה הישנה (אנדרואיד 8–11): DeviceAdmin.onProfileProvisioningComplete.
 */
object Provisioning {
    private const val PREFS = "fp_provisioning"
    private const val K_CODE = "code"
    private const val K_API = "api"

    fun complete(context: Context, extras: PersistableBundle?) {
        val code = extras?.getString("fp_code")
        val api = extras?.getString("fp_api")
        if (!code.isNullOrBlank()) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString(K_CODE, code).putString(K_API, api).apply()
        }
        runCatching { DeviceOwnerManager.applyBaseline(context) }
            .onFailure { Log.w("FilterPhone", "baseline policy failed", it) }
        Recovery.schedulePeriodic(context)
    }

    /** קוד הפעלה שהגיע ב-QR ועוד לא נוצל: {code, api}. */
    fun pending(context: Context): Map<String, String?>? {
        val p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val code = p.getString(K_CODE, null) ?: return null
        return mapOf("code" to code, "api" to p.getString(K_API, null))
    }

    fun clear(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply()

    fun adminExtras(intent: Intent?): PersistableBundle? {
        intent ?: return null
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(DevicePolicyManager.EXTRA_PROVISIONING_ADMIN_EXTRAS_BUNDLE, PersistableBundle::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(DevicePolicyManager.EXTRA_PROVISIONING_ADMIN_EXTRAS_BUNDLE)
        }
    }
}

/** GET_PROVISIONING_MODE: איזה סוג הקמה — תמיד "מכשיר מנוהל במלואו" אם מותר. */
class ProvisioningModeActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        var mode = DevicePolicyManager.PROVISIONING_MODE_FULLY_MANAGED_DEVICE
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val allowed = intent.getIntegerArrayListExtra(DevicePolicyManager.EXTRA_PROVISIONING_ALLOWED_PROVISIONING_MODES)
            if (allowed != null && allowed.isNotEmpty() && mode !in allowed) mode = allowed.first()
        }
        // הקוד נשמר כבר כאן — למקרה שהמערכת לא מעבירה אותו שוב בשלב הבא
        Provisioning.adminExtras(intent)?.let { extras ->
            extras.getString("fp_code")?.let {
                getSharedPreferences("fp_provisioning", MODE_PRIVATE).edit()
                    .putString("code", it).putString("api", extras.getString("fp_api")).apply()
            }
        }
        val result = Intent().putExtra(DevicePolicyManager.EXTRA_PROVISIONING_MODE, mode)
        Provisioning.adminExtras(intent)?.let { result.putExtra(DevicePolicyManager.EXTRA_PROVISIONING_ADMIN_EXTRAS_BUNDLE, it) }
        setResult(RESULT_OK, result)
        finish()
    }
}

/** ADMIN_POLICY_COMPLIANCE: סוף ההקמה — הגדרות הבסיס של מנהל המכשיר, וקוד ההפעלה מה-QR. */
class PolicyComplianceActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        TunnelManager.init(this)
        Provisioning.complete(this, Provisioning.adminExtras(intent))
        setResult(RESULT_OK)
        finish()
    }
}
