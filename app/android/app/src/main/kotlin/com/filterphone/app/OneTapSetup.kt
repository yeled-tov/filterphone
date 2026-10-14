package com.filterphone.app

import android.content.Context
import android.net.VpnService
import android.os.Build
import android.os.PowerManager
import android.util.Log
import com.wireguard.android.backend.Tunnel
import java.util.concurrent.TimeUnit

/**
 * "הקמה בלחיצה אחת" — כל מה שאפשר לעשות בלי שהמשתמש יגע, נעשה כאן; מה שאנדרואיד מחייב בו אישור
 * של המשתמש (בלי root ובלי מנהל מכשיר) — הממשק פותח אחד אחרי השני (AppController.oneTapSetup).
 *
 * עם root (אחרי אישור אחד בחלון של אפליקציית ה-root):
 *   מנהל מכשיר (dpm set-device-owner — אם אנדרואיד מתיר: בלי חשבונות במכשיר), WRITE_SECURE_SETTINGS
 *   (המגן נדלק לבד וחוזר אם מכבים), פטור סוללה ועבודה ברקע, אישור VPN, התראות, ותעודת הסינון.
 * כמנהל מכשיר (גם בלי root, למשל אחרי QR): VPN תמיד מופעל (כולל האישור), התראות ותעודה — בשקט.
 * בלי שניהם: לא עוקפים כלום — מחזירים מה חסר, והממשק מוביל את המשתמש.
 *
 * כל צעד נבדק בפועל בסוף (לא "הצליח" כי הפקודה רצה). חוסם — להריץ בחוט רקע.
 */
object OneTapSetup {
    private const val TAG = "FilterPhone"

    fun run(context: Context, caPem: String?): Map<String, Any?> {
        val pkg = context.packageName
        val out = LinkedHashMap<String, Any?>()
        val root = runCatching { RootProvisioner.hasRoot() }.getOrDefault(false)
        out["root"] = root
        if (root && !DeviceOwnerManager.isDeviceOwner(context)) {
            val r = RootProvisioner.provision(context)
            out["device_owner_result"] = r.resultKey
            out["technical"] = r.technical
        }
        val owner = DeviceOwnerManager.isDeviceOwner(context)
        out["device_owner"] = owner
        if (root) {
            if (!AccessibilityGuard.canHeal(context)) {
                RootProvisioner.runSu("pm grant $pkg android.permission.WRITE_SECURE_SETTINGS", 20)
            }
            // סוללה: מחוץ ל-Doze ובלי הגבלת רקע — כדי שאנדרואיד לא יעצור את הסינון
            RootProvisioner.runSu("dumpsys deviceidle whitelist +$pkg; cmd appops set $pkg RUN_IN_BACKGROUND allow; " +
                "cmd appops set $pkg RUN_ANY_IN_BACKGROUND allow", 20)
            if (!owner) RootProvisioner.runSu("appops set $pkg ACTIVATE_VPN allow", 15)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                RootProvisioner.runSu("pm grant $pkg android.permission.POST_NOTIFICATIONS", 15)
            }
        }
        out["secure_settings"] = AccessibilityGuard.canHeal(context)
        if (owner) runCatching { DeviceOwnerManager.applyBaseline(context) }.onFailure { Log.w(TAG, "baseline failed", it) }
        out["accessibility"] = runCatching { AccessibilityGuard.ensureEnabled(context, "one-tap") }.getOrDefault(false)
        if (caPem != null) {
            ProtectionState.setString(context, ProtectionState.CA_PEM, caPem)
            val installed = runCatching { CertHelper.isInstalled(caPem) }.getOrDefault(false)
            if (!installed) {
                when {
                    owner -> CertHelper.installAsOwner(context, caPem)
                    root -> runCatching { CertHelper.installWithRoot(caPem) }.onFailure { Log.w(TAG, "root CA failed", it) }
                }
            }
            out["ca"] = runCatching { CertHelper.isInstalled(caPem) }.getOrDefault(false)
        }
        out["battery"] = context.getSystemService(PowerManager::class.java)?.isIgnoringBatteryOptimizations(pkg) == true
        val consent = VpnService.prepare(context) == null
        out["vpn_consent"] = consent
        if (consent && TunnelManager.hasConfig() && TunnelManager.state != Tunnel.State.UP) {
            runCatching {
                TunnelManager.connect().get(25, TimeUnit.SECONDS)
                Recovery.schedulePeriodic(context)
            }.onFailure { Log.w(TAG, "one-tap connect failed", it) }
        }
        out["vpn"] = TunnelManager.state == Tunnel.State.UP
        Log.i(TAG, "one-tap setup: root=$root owner=$owner vpn=${out["vpn"]} ca=${out["ca"]}")
        return out
    }
}
