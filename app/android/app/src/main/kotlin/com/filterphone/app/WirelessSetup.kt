package com.filterphone.app

import android.Manifest
import android.accounts.AccountManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import android.provider.Settings
import android.util.Log
import io.github.muntashirakon.adb.AbsAdbConnectionManager
import io.github.muntashirakon.adb.AdbStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * "הקמה בלחיצה אחת" בלי מחשב ובלי אפליקציה חיצונית — דרך **ניפוי אלחוטי** של אנדרואיד (11+),
 * מתוך FilterPhone עצמה (כמו שסוויצ' עושה):
 *
 *   1. המשתמש מדליק "ניפוי באגים אלחוטי" ולוחץ "התאמת מכשיר באמצעות קוד התאמה" — מופיע קוד בן 6 ספרות.
 *   2. מזין את הקוד ב-FilterPhone. את הפורט מגלים לבד ב-mDNS; החיבור אחרי ההתאמה — גם הוא ב-mDNS.
 *   3. FilterPhone מתחברת ל-adbd המקומי ומריצה את פקודת ההקמה הרשמית (dpm set-device-owner),
 *      כולל הטריק של השבתת חשבונות זמנית (AdbCommands) — ואז הרשאות, פטור סוללה, אישור VPN ותעודה.
 *
 * זה בדיוק מה ש-adb במחשב עושה. מה שאנדרואיד לא מתיר — נכשל ומדווח, כמו במסלול ה-root.
 */
object WirelessSetup {
    private const val TAG = "FilterPhone"
    private const val PAIR_SERVICE = "_adb-tls-pairing._tcp"
    private const val CONNECT_TIMEOUT_MS = 20_000L
    private const val CMD_TIMEOUT_MS = 30_000L

    /** ניפוי אלחוטי קיים רק באנדרואיד 11 ומעלה. */
    fun isSupported(): Boolean = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R

    /** פותח את "אפשרויות מפתחים" (משם מגיעים ל"ניפוי באגים אלחוטי"). */
    fun openDeveloperOptions(context: Context): Boolean = runCatching {
        context.startActivity(
            Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
        true
    }.getOrDefault(false)

    /** תקין רק כקוד בן 6 ספרות (כמו שמציג אנדרואיד). */
    fun validCode(code: String): Boolean = code.trim().let { it.length == 6 && it.all(Char::isDigit) }

    /**
     * ההקמה המלאה. חוסם (רשת + I/O) — להריץ בחוט רקע.
     * [code] = קוד ההתאמה בן 6 הספרות; [port] = פורט ההתאמה (0 = לגלות לבד ב-mDNS).
     * מחזיר [RootProvisioner.Outcome] זהה במבנה למסלול ה-root.
     */
    fun provision(context: Context, code: String, port: Int = 0): RootProvisioner.Outcome {
        if (!isSupported()) {
            return RootProvisioner.Outcome(false, "ENROLL-WIFI-001", "wireless_unsupported",
                "Wireless debugging requires Android 11+")
        }
        if (!validCode(code)) {
            return RootProvisioner.Outcome(false, "ENROLL-WIFI-002", "bad_pairing_code")
        }
        if (DeviceOwnerManager.isDeviceOwner(context)) {
            return RootProvisioner.Outcome(true, "ENROLL-WIFI-000", "already_managed")
        }
        val mgr = try {
            AdbManager.get(context)
        } catch (e: Exception) {
            Log.w(TAG, "adb manager init failed", e)
            return RootProvisioner.Outcome(false, "ENROLL-WIFI-003", "adb_init_failed", err(e))
        }
        try {
            // 1) התאמה (pairing)
            val pairPort = port.takeIf { it in 1..65535 } ?: discoverPort(context, PAIR_SERVICE)
            if (pairPort == 0) {
                return RootProvisioner.Outcome(false, "ENROLL-WIFI-004", "pairing_port_not_found",
                    "mDNS did not find the pairing service — enter the port shown under the code")
            }
            val paired = pair(mgr, pairPort, code.trim())
            if (!paired) {
                return RootProvisioner.Outcome(false, "ENROLL-WIFI-005", "pairing_failed",
                    "pair() returned false on port $pairPort")
            }
            // 2) חיבור (mDNS, אנדרואיד 11+)
            val connected = runCatching { mgr.autoConnect(context, CONNECT_TIMEOUT_MS) }
                .onFailure { Log.w(TAG, "autoConnect failed", it) }.getOrDefault(false)
            if (!connected) {
                return RootProvisioner.Outcome(false, "ENROLL-WIFI-006", "connect_failed",
                    "autoConnect returned false after pairing")
            }
            // 3) הקמה: set-device-owner (+ טריק חשבונות), ואז הרשאות ושאר הצעדים
            return runProvision(context, mgr)
        } catch (e: Exception) {
            Log.w(TAG, "wireless provision failed", e)
            return RootProvisioner.Outcome(false, "ENROLL-WIFI-007", "wireless_error", err(e))
        } finally {
            AdbManager.close()
        }
    }

    private fun pair(mgr: AbsAdbConnectionManager, port: Int, code: String): Boolean {
        for (host in listOf("127.0.0.1", "localhost")) {
            val ok = runCatching { mgr.pair(host, port, code) }
                .onFailure { Log.w(TAG, "pair($host:$port) failed", it) }.getOrDefault(false)
            if (ok) return true
        }
        return false
    }

    private fun runProvision(context: Context, mgr: AbsAdbConnectionManager): RootProvisioner.Outcome {
        val pkg = context.packageName
        // ניסיון ישיר; אם נחסם בגלל חשבונות — הטריק (השבתת אפליקציות החשבון זמנית).
        var out = run(mgr, AdbCommands.setDeviceOwner(pkg))
        if (!DeviceOwnerManager.isDeviceOwner(context) && AdbCommands.blockedByAccounts(out)) {
            val accountPkgs = accountPackages(context)
            Log.i(TAG, "device owner blocked by accounts — disabling ${accountPkgs.size} authenticator apps temporarily")
            out = run(mgr, AdbCommands.accountTrick(pkg, accountPkgs))
        }
        if (!DeviceOwnerManager.isDeviceOwner(context)) {
            val reason = RootProvisioner.classifyDpmFailure(out)
            return RootProvisioner.Outcome(false, "ENROLL-WIFI-008", reason, out.take(300))
        }
        // הצלחה: הרשאות ושאר הצעדים
        for (cmd in AdbCommands.followups(pkg, Build.VERSION.SDK_INT, owner = true)) run(mgr, cmd)
        val granted = context.checkSelfPermission(Manifest.permission.WRITE_SECURE_SETTINGS) ==
            PackageManager.PERMISSION_GRANTED
        runCatching { DeviceOwnerManager.applyBaseline(context) }
            .onFailure { Log.w(TAG, "baseline after wireless provision failed", it) }
        Recovery.scheduleDaily(context)
        val technical = "device_owner_set; WRITE_SECURE_SETTINGS=${if (granted) "ok" else "no"}".take(300)
        return RootProvisioner.Outcome(true, "ENROLL-WIFI-000", "device_owner_set", technical)
    }

    /** מריץ פקודת shell אחת דרך ADB וקורא את כל הפלט (חוסם). */
    private fun run(mgr: AbsAdbConnectionManager, cmd: String): String {
        return try {
            val stream: AdbStream = mgr.openStream("shell:$cmd")
            val text = stream.openInputStream().use { it.readBytes().toString(Charsets.UTF_8) }
            runCatching { stream.close() }
            text.trim()
        } catch (e: Exception) {
            Log.w(TAG, "adb command failed: ${cmd.take(40)}", e)
            "exec_error: ${err(e)}"
        }
    }

    /**
     * אפליקציות החשבון שאפשר להשבית זמנית (לטריק). נגזר מרשימת ה-authenticators — בלי הרשאת
     * GET_ACCOUNTS (שאסורה לפי בדיקת ה-APK), ובלי לקרוא את החשבונות עצמם.
     */
    private fun accountPackages(context: Context): List<String> {
        return try {
            val pkgs = AccountManager.get(context).authenticatorTypes.map { it.packageName }
            AdbCommands.accountPackages(pkgs, context.packageName)
        } catch (e: Exception) {
            Log.w(TAG, "could not read authenticators", e)
            emptyList()
        }
    }

    /** מגלה פורט של שירות mDNS (למשל ההתאמה) על המכשיר עצמו. 0 אם לא נמצא בזמן. */
    private fun discoverPort(context: Context, serviceType: String): Int {
        val nsd = context.getSystemService(Context.NSD_SERVICE) as? NsdManager ?: return 0
        val found = AtomicInteger(0)
        val resolved = CountDownLatch(1)
        val listenerRef = AtomicReference<NsdManager.DiscoveryListener?>()

        val resolveListener = object : NsdManager.ResolveListener {
            override fun onResolveFailed(serviceInfo: NsdServiceInfo?, errorCode: Int) {}
            override fun onServiceResolved(serviceInfo: NsdServiceInfo?) {
                val p = serviceInfo?.port ?: 0
                if (p in 1..65535) {
                    found.set(p)
                    resolved.countDown()
                }
            }
        }
        val discoveryListener = object : NsdManager.DiscoveryListener {
            override fun onStartDiscoveryFailed(serviceType: String?, errorCode: Int) { resolved.countDown() }
            override fun onStopDiscoveryFailed(serviceType: String?, errorCode: Int) {}
            override fun onDiscoveryStarted(serviceType: String?) {}
            override fun onDiscoveryStopped(serviceType: String?) {}
            override fun onServiceLost(serviceInfo: NsdServiceInfo?) {}
            override fun onServiceFound(serviceInfo: NsdServiceInfo?) {
                if (serviceInfo != null) runCatching { nsd.resolveService(serviceInfo, resolveListener) }
            }
        }
        return try {
            listenerRef.set(discoveryListener)
            nsd.discoverServices(serviceType, NsdManager.PROTOCOL_DNS_SD, discoveryListener)
            resolved.await(12, TimeUnit.SECONDS)
            found.get()
        } catch (e: Exception) {
            Log.w(TAG, "mDNS discovery failed for $serviceType", e)
            0
        } finally {
            listenerRef.get()?.let { runCatching { nsd.stopServiceDiscovery(it) } }
        }
    }

    private fun err(e: Throwable) = "${e.javaClass.simpleName}: ${e.message.orEmpty()}".take(200)
}
