package com.filterphone.app

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.VpnService
import android.os.SystemClock
import android.util.Log
import com.wireguard.android.backend.BackendException
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import com.wireguard.crypto.Key
import com.wireguard.crypto.KeyPair
import org.json.JSONObject
import java.io.BufferedReader
import java.io.StringReader
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.TimeUnit

/**
 * ניהול מנהרת ה-VPN (WireGuard, מימוש userspace של GoBackend).
 *
 * עובד גם בלי ממשק: באתחול המכשיר (BootReceiver), במצב "VPN תמיד מופעל" של אנדרואיד
 * (alwaysOnCallback), מהשירות הקדמי (ProtectionService) ומהמשימה התקופתית (SyncJob).
 *
 * התאוששות:
 *   - חיבור שנכשל (למשל אין עדיין DNS אחרי אתחול) — ניסיון חוזר עם השהיה הולכת וגדלה (Backoff),
 *     ואם השם לא מתפענח — חיבור לכתובת ה-IP החלופית מהשרת (endpoint_fallback).
 *   - בדיקת תקינות: מנהרה "למעלה" בלי handshake טרי כשיש רשת — מחברים מחדש.
 * לעולם לא בלולאה: כל ניסיון עובר דרך ה-Backoff.
 */
object TunnelManager {
    private const val TAG = "FilterPhone"
    private const val TUNNEL_NAME = "filterphone"
    private const val K_PRIVATE_KEY = "wg_private_key"
    private const val K_CONFIG = "wg_config"           // פורמט ישן: wg-quick מוכן (לפני endpoint_fallback)
    private const val K_VPN_JSON = "wg_vpn_json"       // ההגדרות מהשרת (בלי המפתח הפרטי) — מוצפן
    private const val K_WANTED = "vpn_wanted"
    private const val STALE_HANDSHAKE_MS = 3 * 60_000L // WireGuard מחדש handshake כל 2 דקות כשיש תעבורה
    private const val CONNECT_TIMEOUT_S = 25L

    private lateinit var appContext: Context
    private val backend: GoBackend by lazy { GoBackend(appContext) }
    private val store: SecureStore by lazy { SecureStore(appContext) }
    private val worker: ExecutorService = Executors.newSingleThreadExecutor()
    private val listeners = CopyOnWriteArrayList<(Tunnel.State) -> Unit>()
    private val backoff: Backoff by lazy { Backoff(ProtectionState.prefs(appContext), "vpn") }

    @Volatile var state: Tunnel.State = Tunnel.State.DOWN
        private set
    @Volatile var lastError: String? = null
        private set
    @Volatile private var upSince = 0L

    private val tunnel = object : Tunnel {
        override fun getName() = TUNNEL_NAME
        override fun onStateChange(newState: Tunnel.State) {
            state = newState
            if (newState == Tunnel.State.UP) upSince = SystemClock.elapsedRealtime()
            listeners.forEach { runCatching { it(newState) } }
        }
    }

    fun init(context: Context) {
        if (::appContext.isInitialized) return
        appContext = context.applicationContext
        // המערכת מפעילה את שירות ה-VPN בעצמה (אתחול / תמיד מופעל) — מחברים מההגדרות השמורות.
        // הקריאה מגיעה ב-main thread — החיבור עצמו ברקע
        GoBackend.setAlwaysOnCallback { Recovery.runAsync(appContext) { ensureConnected("always-on") } }
    }

    val isInitialized: Boolean get() = ::appContext.isInitialized

    fun addListener(l: (Tunnel.State) -> Unit) = listeners.add(l)
    fun removeListener(l: (Tunnel.State) -> Unit) = listeners.remove(l)

    // ---------------------------------------------------------------- מפתחות והגדרות

    /** זוג מפתחות חדש. הפרטי נשמר מוצפן ולא יוצא מהמכשיר; מוחזר רק הציבורי. */
    fun newKeyPair(): String {
        val kp = KeyPair()
        store.put(K_PRIVATE_KEY, kp.privateKey.toBase64())
        return kp.publicKey.toBase64()
    }

    fun publicKey(): String? =
        store.get(K_PRIVATE_KEY)?.let { KeyPair(Key.fromBase64(it)).publicKey.toBase64() }

    /** שומר את ההגדרות מהשרת. נבדק שאפשר לבנות מהן הגדרה תקינה לפני ששומרים (בלי לדרוס טובה בפגומה). */
    fun saveConfig(vpnJson: String) {
        val privateKey = store.get(K_PRIVATE_KEY) ?: throw IllegalStateException("no private key")
        val vpn = JSONObject(vpnJson)
        VpnConfigFactory.build(vpn, privateKey, appContext)
        store.put(K_VPN_JSON, vpn.toString())
        store.put(K_CONFIG, null)
    }

    fun hasConfig(): Boolean = store.get(K_VPN_JSON) != null || store.get(K_CONFIG) != null

    fun hasPrivateKey(): Boolean = store.get(K_PRIVATE_KEY) != null

    private fun loadConfig(useFallback: Boolean): Config? {
        val json = store.get(K_VPN_JSON)
        val privateKey = store.get(K_PRIVATE_KEY)
        if (json != null && privateKey != null) {
            return VpnConfigFactory.build(JSONObject(json), privateKey, appContext, useFallback)
        }
        return store.get(K_CONFIG)?.let { VpnConfigFactory.buildLegacy(it, appContext) }
    }

    private fun hasFallback(): Boolean =
        store.get(K_VPN_JSON)?.let { JSONObject(it).optString("endpoint_fallback").isNotEmpty() } ?: false

    // ---------------------------------------------------------------- חיבור

    fun isPermissionGranted(): Boolean = VpnService.prepare(appContext) == null

    fun wantsConnection(): Boolean =
        appContext.getSharedPreferences("fp", Context.MODE_PRIVATE).getBoolean(K_WANTED, false)

    private fun setWanted(wanted: Boolean) =
        appContext.getSharedPreferences("fp", Context.MODE_PRIVATE).edit().putBoolean(K_WANTED, wanted).apply()

    /** חיבור (מהממשק — המשתמש לחץ). מאפס את ההשהיה: המשתמש רוצה עכשיו. */
    fun connect(): Future<Tunnel.State> = worker.submit<Tunnel.State> {
        setWanted(true)
        backoff.succeeded()
        bringUp()
    }

    private fun bringUp(): Tunnel.State {
        // Must refresh the Device Owner lockdown allowlist before reconnecting the full-tunnel VPN.
        // Failure to update this optional bypass must not prevent the VPN itself from connecting.
        runCatching { DeviceOwnerManager.refreshYouTubeLockdownBypass(appContext) }
            .onFailure { Log.w(TAG, "YouTube lockdown bypass refresh failed", it) }
        val config = loadConfig(useFallback = false) ?: throw IllegalStateException("no vpn config")
        try {
            val s = backend.setState(tunnel, Tunnel.State.UP, config)
            onConnected()
            return s
        } catch (e: Exception) {
            if (e.isDnsFailure() && hasFallback()) {
                Log.w(TAG, "endpoint name did not resolve — using the fallback address")
                val s = backend.setState(tunnel, Tunnel.State.UP, loadConfig(useFallback = true)!!)
                onConnected()
                return s
            }
            lastError = e.message ?: e.javaClass.simpleName
            ProtectionState.setString(appContext, ProtectionState.VPN_ERROR, lastError)
            Log.e(TAG, "tunnel up failed", e)
            throw e
        }
    }

    private fun onConnected() {
        lastError = null
        backoff.succeeded()
        ProtectionState.setString(appContext, ProtectionState.VPN_ERROR, null)
    }

    private fun Throwable.isDnsFailure(): Boolean =
        (this is BackendException && reason == BackendException.Reason.DNS_RESOLUTION_FAILURE) ||
            this is java.net.UnknownHostException || cause?.isDnsFailure() == true

    /**
     * מחבר מההגדרות השמורות אם צריך — מכל מקום ברקע (אתחול, always-on, שינוי רשת, משימה תקופתית).
     * מכבד את ה-Backoff; כשל קובע את הניסיון הבא (Recovery מתזמן אותו).
     * מחזיר true אם המנהרה למעלה בסוף.
     */
    fun ensureConnected(reason: String): Boolean {
        if (!isInitialized) return false
        if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) {
            Recovery.runAsync(appContext) { ensureConnected(reason) }   // לעולם לא חוסמים את ה-main thread
            return state == Tunnel.State.UP
        }
        if (!wantsConnection() || !hasConfig()) return false
        if (!isPermissionGranted()) {
            // בלי אישור VPN מהמשתמש אין מה לעשות ברקע — מוצג באפליקציה ובהתראה
            ProtectionState.setString(appContext, ProtectionState.VPN_ERROR, "vpn permission revoked")
            return false
        }
        if (state == Tunnel.State.UP) return true
        if (!backoff.ready()) {
            Recovery.scheduleRetry(appContext, backoff.waitMs())
            return false
        }
        return try {
            worker.submit<Tunnel.State> { bringUp() }.get(CONNECT_TIMEOUT_S, TimeUnit.SECONDS)
            ProtectionState.inc(appContext, ProtectionState.VPN_RECONNECTS)
            Log.i(TAG, "tunnel reconnected ($reason)")
            true
        } catch (e: Exception) {
            val delay = backoff.failed()
            Log.w(TAG, "reconnect ($reason) failed, next try in ${delay / 1000}s: ${e.javaClass.simpleName}")
            Recovery.scheduleRetry(appContext, delay)
            false
        }
    }

    /**
     * בדיקת תקינות: המנהרה למעלה וה-handshake האחרון טרי. מנהרה "תקועה" (יש רשת אבל אין handshake
     * כמה דקות) — מחברים מחדש. נקרא מהשירות הקדמי ומהמשימה התקופתית.
     */
    fun healthCheck(): Boolean {
        if (!isInitialized || !wantsConnection() || !hasConfig()) return false
        if (state != Tunnel.State.UP) return ensureConnected("health")
        val age = handshakeAgeMs()
        if (age != null && age < STALE_HANDSHAKE_MS) {
            ProtectionState.mark(appContext, ProtectionState.VPN_HEALTH_OK)
            return true
        }
        val upFor = SystemClock.elapsedRealtime() - upSince
        if (upFor < STALE_HANDSHAKE_MS || !hasUnderlyingNetwork()) return true   // רק עלתה / אין רשת בכלל
        if (!backoff.ready()) return false
        Log.w(TAG, "tunnel up but no fresh handshake (${age?.div(1000)}s) — restarting")
        return try {
            worker.submit<Tunnel.State> {
                backend.setState(tunnel, Tunnel.State.DOWN, null)
                bringUp()
            }.get(CONNECT_TIMEOUT_S, TimeUnit.SECONDS)
            ProtectionState.inc(appContext, ProtectionState.VPN_RECONNECTS)
            true
        } catch (e: Exception) {
            Recovery.scheduleRetry(appContext, backoff.failed())
            false
        }
    }

    private fun hasUnderlyingNetwork(): Boolean {
        val cm = appContext.getSystemService(ConnectivityManager::class.java) ?: return true
        return cm.allNetworks.any { n ->
            cm.getNetworkCapabilities(n)?.let {
                it.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
                    !it.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
            } ?: false
        }
    }

    fun handshakeAgeMs(): Long? {
        if (state != Tunnel.State.UP) return null
        return try {
            val stats = backend.getStatistics(tunnel)
            val last = stats.peers().maxOfOrNull { stats.peer(it)?.latestHandshakeEpochMillis() ?: 0L } ?: 0L
            if (last > 0) (System.currentTimeMillis() - last).coerceAtLeast(0) else null
        } catch (e: Exception) {
            null
        }
    }

    fun disconnect(): Future<Tunnel.State> = worker.submit<Tunnel.State> {
        setWanted(false)
        backend.setState(tunnel, Tunnel.State.DOWN, null)
    }

    fun status(): Map<String, Any?> {
        var rx = 0L
        var tx = 0L
        var handshake = 0L
        if (state == Tunnel.State.UP) {
            try {
                val stats = backend.getStatistics(tunnel)
                rx = stats.totalRx()
                tx = stats.totalTx()
                handshake = stats.peers().maxOfOrNull { stats.peer(it)?.latestHandshakeEpochMillis() ?: 0L } ?: 0L
            } catch (e: Exception) {
                Log.w(TAG, "statistics failed", e)
            }
        }
        return mapOf(
            "state" to state.name,
            "rx" to rx,
            "tx" to tx,
            "lastHandshakeMs" to handshake,
            "hasConfig" to hasConfig(),
            "hasKey" to hasPrivateKey(),
            "permission" to isPermissionGranted(),
            "alwaysOn" to alwaysOn(),
            "lockdown" to lockdown(),
            "error" to (lastError ?: ProtectionState.string(appContext, ProtectionState.VPN_ERROR)),
            "lastHealthOkMs" to ProtectionState.time(appContext, ProtectionState.VPN_HEALTH_OK),
            "reconnects" to ProtectionState.count(appContext, ProtectionState.VPN_RECONNECTS),
        )
    }

    /** "VPN תמיד מופעל": מהשירות עצמו כשהוא רץ; במנהל מכשיר — גם מ-DevicePolicyManager. null = לא ידוע. */
    fun alwaysOn(): Boolean? =
        DeviceOwnerManager.alwaysOnVpn(appContext) ?: safely { backend.isAlwaysOn }

    fun lockdown(): Boolean? =
        DeviceOwnerManager.alwaysOnLockdown(appContext) ?: safely { backend.isLockdownEnabled }

    /** מחיקת כל ההגדרות (התחלה מחדש). */
    fun reset() {
        try { disconnect().get() } catch (_: Exception) { }
        store.clear()
        backoff.succeeded()
    }

    private fun safely(block: () -> Boolean): Boolean? = try {
        if (state == Tunnel.State.UP) block() else null
    } catch (_: Exception) {
        null
    }
}
