package com.filterphone.app

import android.content.Context
import android.net.VpnService
import android.util.Log
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

/**
 * ניהול מנהרת ה-VPN (WireGuard, מימוש userspace של GoBackend).
 *
 * עובד גם בלי ממשק: באתחול המכשיר (BootReceiver) ובמצב "VPN תמיד מופעל" של אנדרואיד
 * (alwaysOnCallback) המנהרה עולה מההגדרות השמורות, בלי שהאפליקציה פתוחה.
 */
object TunnelManager {
    private const val TAG = "FilterPhone"
    private const val TUNNEL_NAME = "filterphone"
    private const val K_PRIVATE_KEY = "wg_private_key"
    private const val K_CONFIG = "wg_config"
    private const val K_WANTED = "vpn_wanted"

    private lateinit var appContext: Context
    private val backend: GoBackend by lazy { GoBackend(appContext) }
    private val store: SecureStore by lazy { SecureStore(appContext) }
    private val worker: ExecutorService = Executors.newSingleThreadExecutor()
    private val listeners = CopyOnWriteArrayList<(Tunnel.State) -> Unit>()

    @Volatile var state: Tunnel.State = Tunnel.State.DOWN
        private set
    @Volatile var lastError: String? = null
        private set

    private val tunnel = object : Tunnel {
        override fun getName() = TUNNEL_NAME
        override fun onStateChange(newState: Tunnel.State) {
            state = newState
            listeners.forEach { it(newState) }
        }
    }

    fun init(context: Context) {
        if (::appContext.isInitialized) return
        appContext = context.applicationContext
        GoBackend.setAlwaysOnCallback { connectSaved() }
    }

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

    fun saveConfig(vpnJson: String) {
        val privateKey = store.get(K_PRIVATE_KEY) ?: throw IllegalStateException("no private key")
        val config = VpnConfigFactory.build(JSONObject(vpnJson), privateKey)
        store.put(K_CONFIG, config.toWgQuickString())
    }

    fun hasConfig(): Boolean = store.get(K_CONFIG) != null

    private fun loadConfig(): Config? = store.get(K_CONFIG)?.let {
        Config.parse(BufferedReader(StringReader(it)))
    }

    // ---------------------------------------------------------------- חיבור

    fun isPermissionGranted(): Boolean = VpnService.prepare(appContext) == null

    fun connect(): Future<Tunnel.State> = worker.submit<Tunnel.State> {
        appContext.getSharedPreferences("fp", Context.MODE_PRIVATE).edit().putBoolean(K_WANTED, true).apply()
        val config = loadConfig() ?: throw IllegalStateException("no vpn config")
        try {
            val s = backend.setState(tunnel, Tunnel.State.UP, config)
            lastError = null
            s
        } catch (e: Exception) {
            lastError = e.message ?: e.javaClass.simpleName
            Log.e(TAG, "tunnel up failed", e)
            throw e
        }
    }

    /** מחבר מההגדרות השמורות אם יש הרשאה — באתחול ובמצב always-on. */
    fun connectSaved() {
        if (hasConfig() && isPermissionGranted()) {
            connect()
        } else {
            Log.w(TAG, "cannot auto-connect: config=${hasConfig()} permission=${isPermissionGranted()}")
        }
    }

    fun wantsConnection(): Boolean =
        appContext.getSharedPreferences("fp", Context.MODE_PRIVATE).getBoolean(K_WANTED, false)

    fun disconnect(): Future<Tunnel.State> = worker.submit<Tunnel.State> {
        appContext.getSharedPreferences("fp", Context.MODE_PRIVATE).edit().putBoolean(K_WANTED, false).apply()
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
            "permission" to isPermissionGranted(),
            "alwaysOn" to safely { backend.isAlwaysOn },
            "lockdown" to safely { backend.isLockdownEnabled },
            "error" to lastError,
        )
    }

    /** מחיקת כל ההגדרות (התחלה מחדש). */
    fun reset() {
        try { disconnect().get() } catch (_: Exception) { }
        store.clear()
    }

    private fun safely(block: () -> Boolean): Boolean? = try {
        if (state == Tunnel.State.UP) block() else null
    } catch (_: Exception) {
        null
    }
}
