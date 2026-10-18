package com.filterphone.app

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.VpnService
import android.os.Build
import android.os.SystemClock
import android.util.Log
import com.wireguard.android.backend.BackendException
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import com.wireguard.crypto.Key
import com.wireguard.crypto.KeyPair
import org.json.JSONObject
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
 *   - בדיקת תעבורה (DataProbe): handshake טרי לא מוכיח שיש אינטרנט. בקשת HTTP אמיתית במנהרה, לכל
 *     היותר פעם בדקה; שני כשלים ברצף כשהרשת של הטלפון עצמה תקינה — מחברים מחדש.
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
    private const val DATA_CHECK_EVERY_MS = 60_000L    // בדיקת תעבורה — לכל היותר פעם בדקה (סוללה)
    private const val DATA_FAILS_TO_RECONNECT = 2      // כשל בודד יכול להיות רגעי — לא מחברים מחדש עליו
    private const val K_UPDATE_PAUSE = "vpn_update_pause_until"
    private const val UPDATE_PAUSE_MS = 2 * 60_000L     // לכל היותר — אחר כך מתחברים גם אם לא הגיע אישור

    private lateinit var appContext: Context
    private val backend: GoBackend by lazy { GoBackend(appContext) }
    private val store: SecureStore by lazy { SecureStore(appContext) }
    private val worker: ExecutorService = Executors.newSingleThreadExecutor()
    private val listeners = CopyOnWriteArrayList<(Tunnel.State) -> Unit>()
    private val backoff: Backoff by lazy { Backoff(ProtectionState.prefs(appContext), "vpn") }
    /** חיבור מחדש בגלל "אין תעבורה" — השהיה נפרדת: אם הבעיה בשרת, חיבור מחדש לא יעזור ולא חוזרים עליו כל דקה. */
    private val dataBackoff: Backoff by lazy {
        Backoff(ProtectionState.prefs(appContext), "vpn_data", longArrayOf(120_000, 300_000, 600_000, 900_000))
    }
    @Volatile private var lastDataCheck = 0L
    @Volatile private var dataFailsInRow = 0

    @Volatile var state: Tunnel.State = Tunnel.State.DOWN
        private set
    @Volatile var lastError: String? = null
        private set
    @Volatile private var upSince = 0L
    /** האפליקציות שהוחרגו מהמנהרה בחיבור הנוכחי (VpnBypass) — כדי לזהות שצריך לבנות מחדש. */
    @Volatile var appliedBypass: Set<String> = emptySet()
        private set

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
        VpnConfigFactory.build(vpn, privateKey)
        store.put(K_VPN_JSON, vpn.toString())
        store.put(K_CONFIG, null)
    }

    fun hasConfig(): Boolean = store.get(K_VPN_JSON) != null || store.get(K_CONFIG) != null

    fun hasPrivateKey(): Boolean = store.get(K_PRIVATE_KEY) != null

    private fun loadConfig(useFallback: Boolean, excluded: Set<String>): Config? {
        val json = store.get(K_VPN_JSON)
        val privateKey = store.get(K_PRIVATE_KEY)
        if (json != null && privateKey != null) {
            return VpnConfigFactory.build(JSONObject(json), privateKey, excluded, useFallback)
        }
        return store.get(K_CONFIG)?.let { VpnConfigFactory.buildLegacy(it, excluded) }
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
        endUpdatePause()
        // בהשהיה מאושרת / ברשת כשרה (נטפרי) — ה-VPN כבוי (כבר בחוט של המנהרה: בלי kosherGate שממתין לו)
        if (AdminState.isPaused(appContext) || KosherNet.isActive(appContext)) state else bringUp()
    }

    /**
     * השהיה שהמנהל אישר: הטלפון פתוח — המנהרה יורדת (הרצון לחיבור נשמר, כדי שבסוף ההשהיה הכול
     * יחזור לבד). רץ בחוט של המנהרה. מחזיר true אם ההשהיה בתוקף.
     */
    private fun pausedGate(): Boolean {
        if (!AdminState.isPaused(appContext)) return false
        if (state != Tunnel.State.DOWN) {
            runCatching { worker.submit<Tunnel.State> { backend.setState(tunnel, Tunnel.State.DOWN, null) }.get(10, TimeUnit.SECONDS) }
                .onFailure { Log.w(TAG, "tunnel down for pause failed", it) }
            appliedBypass = emptySet()
            Log.i(TAG, "protection paused by the administrator — tunnel down until the pause ends")
        }
        return true
    }

    /**
     * ברשת כשרה (נטפרי וכו' — KosherNet): הספק חוסם את ה-WireGuard ומסנן בעצמו, אז המנהרה יורדת רק
     * ברשת הזו (הרצון לחיבור נשמר). ביציאה ממנה KosherNet מחבר מחדש. מחזיר true אם המצב בתוקף.
     */
    private fun kosherGate(): Boolean {
        if (!KosherNet.isActive(appContext)) return false
        if (state != Tunnel.State.DOWN) {
            runCatching { worker.submit<Tunnel.State> { backend.setState(tunnel, Tunnel.State.DOWN, null) }.get(10, TimeUnit.SECONDS) }
                .onFailure { Log.w(TAG, "tunnel down for the kosher network failed", it) }
            appliedBypass = emptySet()
            Log.i(TAG, "kosher network — tunnel down on this network only")
        }
        return true
    }

    // ---------------------------------------------------------------- עדכון האפליקציה באנדרואיד 16

    /**
     * אנדרואיד 16 (API 36): עדכון של אפליקציית VPN בזמן שה-VPN שלה פעיל משאיר אותה בלי רשת עד
     * הפעלה מחדש של הטלפון — באג של אנדרואיד (Google issuetracker 441315112 / 447433152, דווח על ידי
     * Mullvad, Proton ועוד; לא קורה באנדרואיד 17). ה-VPN "מחובר" ואין אינטרנט, וגם האפליקציה עצמה לא
     * מגיעה לשרת. הדרך שהם ממליצים עליה: לעדכן כשה-VPN כבוי. לכן ממש לפני ההתקנה מורידים את המנהרה
     * (הרצון לחיבור נשמר), ואחרי העדכון (MY_PACKAGE_REPLACED), כישלון / ביטול, או 2 דקות — מתחברים.
     * ב-lockdown אין דליפה: בלי מנהרה אנדרואיד חוסם את התעבורה.
     */
    val updateBugAffected: Boolean get() = Build.VERSION.SDK_INT == 36

    /** מוריד את המנהרה לקראת התקנת עדכון (רק באנדרואיד 16). רץ ברקע — לא ב-main thread. */
    fun pauseForUpdate() {
        if (!isInitialized || !updateBugAffected) return
        prefs().edit().putLong(K_UPDATE_PAUSE, System.currentTimeMillis() + UPDATE_PAUSE_MS).commit()
        if (state != Tunnel.State.DOWN) {
            runCatching { worker.submit<Tunnel.State> { backend.setState(tunnel, Tunnel.State.DOWN, null) }.get(10, TimeUnit.SECONDS) }
                .onFailure { Log.w(TAG, "tunnel down before the update failed", it) }
            appliedBypass = emptySet()
        }
        Log.i(TAG, "tunnel down for the app update (Android 16 VPN-update bug)")
    }

    /** העדכון הסתיים / נכשל / בוטל: מבטל את ההשהיה. true אם הייתה כזו (ואז כדאי לחבר). */
    fun endUpdatePause(): Boolean {
        if (!isInitialized) return false
        val p = prefs()
        if (p.getLong(K_UPDATE_PAUSE, 0L) == 0L) return false
        p.edit().remove(K_UPDATE_PAUSE).commit()
        return true
    }

    private fun updatePaused(): Boolean = System.currentTimeMillis() < prefs().getLong(K_UPDATE_PAUSE, 0L)

    private fun prefs() = appContext.getSharedPreferences("fp", Context.MODE_PRIVATE)

    private fun bringUp(): Tunnel.State {
        // רץ בחוט של המנהרה, כמו ההורדה ב-kosherGate — כך חיבור שהתחיל לפני הזיהוי לא "מנצח" אותו
        if (KosherNet.isActive(appContext)) return state
        // האפליקציות שמחוץ למנהרה (FilterTube וכו') — רק מה שמותקן ועבר את בדיקת החתימה.
        // קודם רשימת ההיתר של lockdown (אחרת אנדרואיד חוסם להן את הרשת), ואז המנהרה עצמה.
        // כשל בהחרגה לעולם לא מונע את ה-VPN: במקרה הגרוע — בלי החרגה (הכול בסינון).
        val bypass = runCatching { VpnBypass.current(appContext) }
            .onFailure { Log.w(TAG, "vpn bypass check failed", it) }.getOrDefault(VpnBypass.Decision.NONE)
        runCatching { DeviceOwnerManager.refreshLockdownAllowlist(appContext, bypass.excluded) }
            .onFailure { Log.w(TAG, "lockdown allowlist refresh failed", it) }
        val config = loadConfig(useFallback = false, bypass.excluded) ?: throw IllegalStateException("no vpn config")
        try {
            val s = backend.setState(tunnel, Tunnel.State.UP, config)
            onConnected(bypass.excluded)
            return s
        } catch (e: Exception) {
            if (e.isDnsFailure() && hasFallback()) {
                Log.w(TAG, "endpoint name did not resolve — using the fallback address")
                val s = backend.setState(tunnel, Tunnel.State.UP, loadConfig(useFallback = true, bypass.excluded)!!)
                onConnected(bypass.excluded)
                return s
            }
            lastError = e.message ?: e.javaClass.simpleName
            ProtectionState.setString(appContext, ProtectionState.VPN_ERROR, lastError)
            Log.e(TAG, "tunnel up failed", e)
            throw e
        }
    }

    private fun onConnected(excluded: Set<String>) {
        lastError = null
        appliedBypass = excluded
        backoff.succeeded()
        ProtectionState.setString(appContext, ProtectionState.VPN_ERROR, null)
    }

    /**
     * רשימת האפליקציות שמחוץ למנהרה השתנתה (מדיניות חדשה, התקנה/הסרה/עדכון של FilterTube) —
     * בונים את החיבור מחדש. GoBackend מוריד ומעלה עם ההגדרה החדשה; ב-lockdown אין דליפה בזמן הזה.
     * לא עושה כלום אם המנהרה למטה (החיבור הבא ייבנה ממילא עם הרשימה העדכנית) או שאין שינוי.
     */
    fun refreshBypass(reason: String) {
        if (!isInitialized || state != Tunnel.State.UP || !wantsConnection()) return
        val want = runCatching { VpnBypass.current(appContext).excluded }.getOrNull() ?: return
        if (want == appliedBypass) return
        Log.i(TAG, "vpn bypass changed ($reason): ${appliedBypass.size} → ${want.size} apps — reconnecting")
        worker.execute {
            runCatching { bringUp() }.onFailure {
                Log.w(TAG, "reconnect after bypass change failed", it)
                Recovery.scheduleRetry(appContext, backoff.failed())
            }
        }
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
        if (pausedGate()) return false
        if (kosherGate()) return false                       // ברשת כשרה — הסינון של הספק פעיל
        if (updatePaused()) return false                     // מותקן עדכון (אנדרואיד 16) — מתחברים אחריו
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
     * בדיקת תקינות: המנהרה למעלה, ה-handshake האחרון טרי, ותעבורה באמת עוברת בה (dataCheck).
     * מנהרה "תקועה" (יש רשת אבל אין handshake כמה דקות) — מחברים מחדש. נקרא מהשירות הקדמי
     * (הפעלה, מסך נדלק, שינוי רשת) ומהמשימה התקופתית. רץ ברקע — לא ב-main thread.
     */
    fun healthCheck(): Boolean {
        if (!isInitialized || !wantsConnection() || !hasConfig()) return false
        if (pausedGate()) return true                        // מושהה בכוונה — "תקין"
        if (kosherGate()) return true                        // ברשת כשרה — בכוונה
        if (updatePaused()) return true                      // מותקן עדכון — מתחברים אחריו
        if (state != Tunnel.State.UP) return ensureConnected("health")
        val age = handshakeAgeMs()
        if (age != null && age < STALE_HANDSHAKE_MS) {
            ProtectionState.mark(appContext, ProtectionState.VPN_HEALTH_OK)
            refreshBypass("health")            // רשת ביטחון: התקנה/הסרה שלא נקלטה בזמן אמת
            return dataCheck()
        }
        val upFor = SystemClock.elapsedRealtime() - upSince
        if (upFor < STALE_HANDSHAKE_MS || !hasUnderlyingNetwork()) return true   // רק עלתה / אין רשת בכלל
        UpdateBugNotice.maybeShow(appContext)
        if (!backoff.ready()) return false
        return reconnect("no fresh handshake (${age?.div(1000)}s)")
    }

    /**
     * תעבורה אמיתית במנהרה (DataProbe) — לכל היותר פעם בדקה. כשל נספר רק כשהרשת של הטלפון עצמה
     * מאומתת אצל אנדרואיד (אחרת אין אינטרנט בכלל — לא אשמת המנהרה). כל כשל נשמר ומדווח לשרת;
     * שני כשלים ברצף — חיבור מחדש (עם השהיה נפרדת שגדלה, כדי שתקלה בשרת לא תיצור לולאה).
     */
    private fun dataCheck(): Boolean {
        val now = SystemClock.elapsedRealtime()
        if (lastDataCheck != 0L && now - lastDataCheck < DATA_CHECK_EVERY_MS) return dataFailsInRow == 0
        lastDataCheck = now
        val failure = DataProbe.run()
        ProtectionState.mark(appContext, ProtectionState.VPN_DATA_CHECKED)
        if (failure == null) {
            if (dataFailsInRow > 0) Log.i(TAG, "tunnel traffic flows again after $dataFailsInRow failed checks")
            dataFailsInRow = 0
            dataBackoff.succeeded()
            ProtectionState.mark(appContext, ProtectionState.VPN_DATA_OK)
            return true
        }
        if (failure == "blocked" || !underlyingValidated()) return true   // לא ניתן להסיק — לא מחברים מחדש
        dataFailsInRow++
        Log.w(TAG, "tunnel up with a fresh handshake but no traffic ($failure), $dataFailsInRow in a row")
        ProtectionState.mark(appContext, ProtectionState.VPN_DATA_FAIL)
        ProtectionState.setString(appContext, ProtectionState.VPN_DATA_ERROR, failure)
        ProtectionState.inc(appContext, ProtectionState.VPN_DATA_FAILS)
        UpdateBugNotice.maybeShow(appContext)
        if (dataFailsInRow < DATA_FAILS_TO_RECONNECT || !dataBackoff.ready() || !backoff.ready()) return false
        dataBackoff.failed()
        lastDataCheck = 0L                       // אחרי החיבור מחדש — בודקים שוב בהזדמנות הבאה
        return reconnect("no traffic: $failure")
    }

    /** מוריד ומעלה את המנהרה (handshake חדש, ואם השם לא מתפענח — הכתובת החלופית). */
    private fun reconnect(why: String): Boolean {
        Log.w(TAG, "tunnel unhealthy ($why) — restarting")
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

    /** לפחות רשת אחת שאינה VPN שאנדרואיד אימת שיש בה אינטרנט (NET_CAPABILITY_VALIDATED). */
    private fun underlyingValidated(): Boolean {
        val cm = appContext.getSystemService(ConnectivityManager::class.java) ?: return false
        return cm.allNetworks.any { n ->
            cm.getNetworkCapabilities(n)?.let {
                it.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED) &&
                    !it.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
            } ?: false
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
        appliedBypass = emptySet()
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
            "dataOkMs" to ProtectionState.time(appContext, ProtectionState.VPN_DATA_OK),
            "dataFailMs" to ProtectionState.time(appContext, ProtectionState.VPN_DATA_FAIL),
            "dataError" to ProtectionState.string(appContext, ProtectionState.VPN_DATA_ERROR),
            "bypass" to appliedBypass.toList(),
            "kosherNet" to KosherNet.provider(appContext),
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
