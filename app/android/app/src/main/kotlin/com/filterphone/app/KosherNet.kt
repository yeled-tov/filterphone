package com.filterphone.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.admin.DevicePolicyManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Build
import android.util.Base64
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.net.Inet4Address
import java.net.InetSocketAddress
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/**
 * רשת כשרה (נטפרי, ובתעודות שהמנהל הוסיף בדשבורד — רימון / אתרוג וכו'): מעבר אוטומטי.
 *
 * הבעיה: ספק כזה חוסם תעבורה שהוא לא יכול לבדוק — גם את ה-WireGuard שלנו. ב"חסימה בלי VPN" זה
 * אומר שאין אינטרנט בכלל ברשת הזו. הפתרון: כשהטלפון ברשת של ספק כשר, ה-VPN שלנו מושהה רק ברשת הזו
 * (שם הסינון של הספק פעיל), ותעודת הספק מותקנת (מנהל מכשיר — בשקט) כדי שהגלישה המאובטחת תעבוד.
 * חסימות האפליקציות והמגן ממשיכים כרגיל. ביציאה מהרשת — "תמיד מופעל" + lockdown חוזרים והמנהרה עולה.
 *
 * הזיהוי לא ניתן לזיוף: חיבור TLS אמיתי לאתר מוכר (מחוץ למנהרה, דרך הרשת עצמה) שנחשב תקין רק אם
 * השרשרת חתומה בתעודת השורש של הספק (PKIX מלא) — וה-handshake הושלם, כלומר מי שענה מחזיק במפתח
 * של התעודה שהציג (שרשרת "מוקלטת" לא עוברת). רשת שמציגה תעודה של מישהו אחר — לא נחשבת כשרה.
 *
 * מה הופך את זה לבטוח גם כשמשהו נתקע: המצב קשור לרשת המסוימת (networkHandle). כל רשת אחרת, אתחול,
 * כיבוי ההגדרה מהדשבורד או השהיה — והמצב לא בתוקף (isActive), אז ה-VPN חוזר. בזמן שבמצב הזה,
 * אנדרואיד מעיר אותנו על כל רשת חדשה גם אם התהליך מת (registerNetworkCallback עם PendingIntent).
 */
object KosherNet {
    private const val TAG = "FilterPhone"
    private const val PREFS = "fp_kosher"
    private const val K_PROVIDER = "provider"
    private const val K_HANDLE = "network_handle"
    private const val K_SINCE = "since"
    private const val K_VERIFIED = "verified_at"
    private const val K_UNKNOWN = "unknown"            // סינון לא מוכר שזוהה (לאבחון בדשבורד)
    private const val K_UNKNOWN_AT = "unknown_at"
    private const val K_EXTRA = "extra_certs"          // מהשרת: [{"provider", "pem"}]
    private const val K_INSTALLED = "installed"        // טביעות של תעודות שאנחנו התקנו (כדי להסיר רק אותן)
    private const val K_NOTICE_HANDLE = "notice_handle"
    const val DPC_KEY = "kosher_network"
    private const val NETFREE = "netfree"
    /** אתרים שכל ספק כשר מאפשר ופותח לבדיקה. */
    private val PROBE_HOSTS = listOf("www.google.com", "www.wikipedia.org")
    private const val TIMEOUT_MS = 6_000
    private const val REVERIFY_MS = 30 * 60_000L       // בדיקה חוזרת של אותה רשת (גם בהדלקת מסך) — לכל היותר
    private const val UNKNOWN_KEEP_MS = 7 * 24 * 3600_000L
    private const val MAX_EXTRA = 20
    private const val WATCH_REQUEST = 7410
    private const val NOTICE_ID = 7411
    private const val CHANNEL_ID = "alerts"

    /** שם לתצוגה בהתראה ובאפליקציה. */
    val LABELS = mapOf(NETFREE to "נטפרי", "rimon" to "רימון", "netspark" to "נטספארק", "other" to "הסינון הכשר")

    class Provider(val id: String, val roots: List<X509Certificate>) {
        val trustManager: X509TrustManager by lazy { trustManagerFor(roots) }
    }

    data class ProbeResult(val provider: String?, val unknownIssuer: String?, val reached: Boolean)

    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "fp-kosher").apply { isDaemon = true } }
    private val running = AtomicBoolean(false)
    @Volatile private var again: String? = null
    @Volatile private var plainHandle = 0L             // רשת שנבדקה ואינה כשרה — לא בודקים שוב כל אירוע
    @Volatile private var plainAt = 0L
    @Volatile private var cachedRoots: Pair<String, List<Provider>>? = null

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    // ---------------------------------------------------------------- מצב

    /** המנהל מרשה (dpc.kosher_network — ברירת מחדל: כן). */
    fun enabled(context: Context): Boolean =
        GuardPolicy.get(context).dpc?.optBoolean(DPC_KEY, true) ?: true

    /** ברשת כשרה עכשיו: אותה רשת שזוהתה, ההגדרה פעילה, ואין השהיה (בהשהיה הכול פתוח ממילא). */
    fun isActive(context: Context): Boolean {
        val p = prefs(context)
        val handle = p.getLong(K_HANDLE, 0L)
        if (handle == 0L || p.getString(K_PROVIDER, null) == null) return false
        if (!enabled(context)) return false
        return defaultUnderlying(context)?.networkHandle == handle
    }

    fun provider(context: Context): String? = if (isActive(context)) prefs(context).getString(K_PROVIDER, null) else null

    fun label(context: Context): String = LABELS[provider(context)] ?: LABELS.getValue("other")

    /** לדיווח לשרת (heartbeat → vpn). */
    fun report(context: Context, vpn: JSONObject) {
        val active = isActive(context)
        vpn.put("kosher", active)
        if (active) prefs(context).getString(K_PROVIDER, null)?.let { vpn.put("kosher_provider", it) }
        val p = prefs(context)
        val unknown = p.getString(K_UNKNOWN, null)
        if (unknown != null && System.currentTimeMillis() - p.getLong(K_UNKNOWN_AT, 0L) < UNKNOWN_KEEP_MS) {
            vpn.put("kosher_unknown", unknown.take(80))
        }
    }

    /** רשימת התעודות שהמנהל הוסיף בדשבורד (בתשובת ה-heartbeat). null = שרת ישן — לא נוגעים. */
    fun saveExtraCerts(context: Context, certs: JSONArray?) {
        if (certs == null) return
        val out = JSONArray()
        for (i in 0 until minOf(certs.length(), MAX_EXTRA)) {
            val o = certs.optJSONObject(i) ?: continue
            val provider = o.optString("provider").takeIf { it.matches(Regex("^[a-z]{2,20}$")) } ?: continue
            val pem = o.optString("pem")
            if (parsePem(pem).isEmpty()) continue
            out.put(JSONObject().put("provider", provider).put("pem", pem))
        }
        val p = prefs(context)
        if (p.getString(K_EXTRA, "[]") != out.toString()) p.edit().putString(K_EXTRA, out.toString()).apply()
    }

    // ---------------------------------------------------------------- תעודות

    /** נטפרי (מובנית — res/raw) + מה שהמנהל הוסיף, לפי ספק. */
    fun providers(context: Context): List<Provider> {
        val extra = prefs(context).getString(K_EXTRA, "[]") ?: "[]"
        cachedRoots?.let { (key, list) -> if (key == extra) return list }
        val byProvider = LinkedHashMap<String, MutableList<X509Certificate>>()
        runCatching {
            context.resources.openRawResource(R.raw.kosher_netfree).bufferedReader().use { it.readText() }
        }.getOrNull()?.let { byProvider.getOrPut(NETFREE) { mutableListOf() } += parsePem(it) }
        runCatching { JSONArray(extra) }.getOrNull()?.let { a ->
            for (i in 0 until a.length()) {
                val o = a.optJSONObject(i) ?: continue
                byProvider.getOrPut(o.optString("provider")) { mutableListOf() } += parsePem(o.optString("pem"))
            }
        }
        val list = byProvider.filterValues { it.isNotEmpty() }.map { (id, roots) -> Provider(id, roots.distinct()) }
        cachedRoots = extra to list
        return list
    }

    /** כל בלוקי ה-PEM בטקסט (מתעלם מהערות). טהור — נבדק ביחידה. */
    fun parsePem(text: String): List<X509Certificate> {
        val cf = CertificateFactory.getInstance("X.509")
        return Regex("-----BEGIN CERTIFICATE-----([A-Za-z0-9+/=\\s]+)-----END CERTIFICATE-----").findAll(text)
            .mapNotNull { m ->
                runCatching {
                    val der = decodeBase64(m.groupValues[1].filterNot { it.isWhitespace() })
                    cf.generateCertificate(ByteArrayInputStream(der)) as X509Certificate
                }.getOrNull()
            }.toList()
    }

    private fun decodeBase64(s: String): ByteArray =
        runCatching { java.util.Base64.getDecoder().decode(s) }.getOrElse { Base64.decode(s, Base64.DEFAULT) }

    fun trustManagerFor(roots: List<X509Certificate>): X509TrustManager {
        val ks = KeyStore.getInstance(KeyStore.getDefaultType()).apply {
            load(null, null)
            roots.forEachIndexed { i, c -> setCertificateEntry("kosher$i", c) }
        }
        val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(ks) }
        return tmf.trustManagers.filterIsInstance<X509TrustManager>().first()
    }

    private fun systemTrustManager(): X509TrustManager {
        val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(null as KeyStore?) }
        return tmf.trustManagers.filterIsInstance<X509TrustManager>().first()
    }

    /**
     * איזה ספק חתם על השרשרת (בדיקת PKIX מלאה מול תעודות השורש של כל ספק). null = אף אחד.
     * טהור (בלי רשת) — נבדק ביחידה.
     */
    fun signedBy(chain: Array<X509Certificate>, authType: String, providers: List<Provider>): String? =
        providers.firstOrNull { p -> runCatching { p.trustManager.checkServerTrusted(chain, authType) }.isSuccess }?.id

    fun fingerprint(c: X509Certificate): String =
        MessageDigest.getInstance("SHA-256").digest(c.encoded).joinToString("") { "%02x".format(it) }

    /** השם של מי שהנפיק את התעודה העליונה בשרשרת — לאבחון ("סינון לא מוכר: ..."). */
    fun issuerHint(chain: Array<X509Certificate>?): String? {
        val top = chain?.lastOrNull() ?: return null
        val dn = top.issuerX500Principal.name
        val cn = Regex("(?:^|,)CN=([^,]+)").find(dn)?.groupValues?.get(1)
        val o = Regex("(?:^|,)O=([^,]+)").find(dn)?.groupValues?.get(1)
        return listOfNotNull(o, cn).distinct().joinToString(" · ").ifEmpty { dn }.take(80)
    }

    // ---------------------------------------------------------------- רשת

    /** הרשת "האמיתית" שהטלפון גולש בה (לא ה-VPN). Wi-Fi / Ethernet קודם, ואז מאומתת. */
    fun defaultUnderlying(context: Context): Network? {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return null
        val active = cm.activeNetwork
        val activeCaps = active?.let { cm.getNetworkCapabilities(it) }
        if (active != null && activeCaps != null && !activeCaps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) &&
            activeCaps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) return active
        return cm.allNetworks.mapNotNull { n -> cm.getNetworkCapabilities(n)?.let { n to it } }
            .filter { (_, c) ->
                !c.hasTransport(NetworkCapabilities.TRANSPORT_VPN) && c.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            }
            .sortedWith(compareBy({ (_, c) ->
                if (c.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || c.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) 0 else 1
            }, { (_, c) -> if (c.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)) 0 else 1 }))
            .firstOrNull()?.first
    }

    /** בדיקת הרשת: חיבור TLS אמיתי, מחוץ למנהרה (socket שקשור לרשת). רץ ברקע. */
    fun probe(network: Network, providers: List<Provider>): ProbeResult {
        var unknown: String? = null
        var reached = false
        for (host in PROBE_HOSTS) {
            val r = probeHost(network, host, providers)
            if (r.provider != null) return r
            if (r.reached) {
                reached = true
                if (r.unknownIssuer == null) return r           // אינטרנט רגיל — תעודה ציבורית אמיתית
                unknown = unknown ?: r.unknownIssuer
            }
        }
        return ProbeResult(null, unknown, reached)
    }

    private fun probeHost(network: Network, host: String, providers: List<Provider>): ProbeResult {
        val addr = runCatching {
            network.getAllByName(host).let { all -> all.firstOrNull { it is Inet4Address } ?: all.firstOrNull() }
        }.getOrNull() ?: return ProbeResult(null, null, false)
        val captured = AtomicReference<Array<X509Certificate>?>()
        val matched = AtomicReference<String?>()
        val system = runCatching { systemTrustManager() }.getOrNull()
        // לא "מקבל הכול": שרשרת עוברת רק אם ספק כשר חתם עליה, או שהיא ציבורית אמיתית (אינטרנט רגיל)
        val tm = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
                throw CertificateException("no client certificates")

            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                captured.set(chain)
                signedBy(chain, authType, providers)?.let { matched.set(it); return }
                (system ?: throw CertificateException("no system trust")).checkServerTrusted(chain, authType)
            }

            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }
        return try {
            val ctx = SSLContext.getInstance("TLS").apply { init(null, arrayOf(tm), null) }
            network.socketFactory.createSocket().use { raw ->
                raw.connect(InetSocketAddress(addr, 443), TIMEOUT_MS)
                raw.soTimeout = TIMEOUT_MS
                (ctx.socketFactory.createSocket(raw, host, 443, true) as SSLSocket).use { ssl ->
                    ssl.startHandshake()     // הושלם = מי שענה מחזיק במפתח של התעודה (לא שרשרת מוקלטת)
                    if (!HttpsURLConnection.getDefaultHostnameVerifier().verify(host, ssl.session)) {
                        ProbeResult(null, issuerHint(captured.get()) ?: "?", true)
                    } else {
                        ProbeResult(matched.get(), null, true)
                    }
                }
            }
        } catch (e: javax.net.ssl.SSLException) {
            // השרשרת נדחתה: יש מישהו באמצע שאינו ספק מוכר (או שאין לנו את התעודה שלו)
            val chain = captured.get()
            if (chain != null) ProbeResult(null, issuerHint(chain) ?: "?", true) else ProbeResult(null, null, false)
        } catch (e: Exception) {
            ProbeResult(null, null, false)
        }
    }

    // ---------------------------------------------------------------- החלטה

    enum class Step { NONE, STAY, ENTER, EXIT }

    /**
     * מה לעשות. טהור — נבדק ביחידה.
     * active: הרשת שבמצב כשר כרגע (null = לא במצב). current: הרשת עכשיו. kosher: תוצאת הבדיקה של
     * current (null = לא נבדקה / לא הגענו לאינטרנט). tunnelWorks: ה-VPN שלנו עובד גם ברשת הזו.
     */
    fun decide(enabled: Boolean, active: Long?, current: Long?, kosher: Boolean?, tunnelWorks: Boolean): Step = when {
        !enabled || current == null -> if (active != null) Step.EXIT else Step.NONE
        active != null && active != current -> Step.EXIT                 // רשת אחרת — מיד, בלי לחכות לבדיקה
        active == current && kosher != false -> Step.STAY                // אותה רשת (וגם כשלא הצלחנו לבדוק)
        active == current -> Step.EXIT                                   // אותה רשת, אבל כבר לא כשרה
        kosher == true && !tunnelWorks -> Step.ENTER
        else -> Step.NONE
    }

    /** בדיקה ברקע (שינוי רשת, הדלקת מסך, הפעלה). פעם אחת בכל רגע — אירוע שהגיע באמצע רץ אחריו. */
    fun evaluateAsync(context: Context, why: String, done: (() -> Unit)? = null) {
        val app = context.applicationContext
        if (!running.compareAndSet(false, true)) {
            again = why
            done?.invoke()
            return
        }
        worker.execute {
            try {
                var reason: String? = why
                while (reason != null) {
                    again = null
                    runCatching { evaluate(app, reason) }.onFailure { Log.w(TAG, "kosher network check failed", it) }
                    reason = again
                }
            } finally {
                running.set(false)
                done?.invoke()
            }
        }
    }

    /** הבדיקה עצמה — לא ב-main thread. */
    fun evaluate(context: Context, why: String) {
        val app = context.applicationContext
        if (!TunnelManager.isInitialized) TunnelManager.init(app)
        val p = prefs(app)
        val active = p.getLong(K_HANDLE, 0L).takeIf { it != 0L && p.getString(K_PROVIDER, null) != null }
        val on = enabled(app) && GuardPolicy.credentials(app) != null && TunnelManager.wantsConnection()
        if (!on) cleanupCerts(app)
        val net = defaultUnderlying(app)
        val current = net?.networkHandle
        // שלב 1 — בלי בדיקה: יציאה מיידית (רשת אחרת / אין רשת / כובה), או אותה רשת שנבדקה לא מזמן
        val quick = decide(on, active, current, kosher = null, tunnelWorks = false)
        if (quick == Step.EXIT) return exit(app, if (!on) "disabled" else if (current == null) "no network" else "network changed ($why)")
        if (!on || net == null || current == null) return
        val now = System.currentTimeMillis()
        if (quick == Step.STAY && now - p.getLong(K_VERIFIED, 0L) < REVERIFY_MS) return
        if (active == null && current == plainHandle && now - plainAt < REVERIFY_MS) return
        // שלב 2 — בדיקת הרשת
        val r = probe(net, providers(app))
        if (r.unknownIssuer != null) {
            p.edit().putString(K_UNKNOWN, r.unknownIssuer).putLong(K_UNKNOWN_AT, now).apply()
        } else if (r.provider != null) {
            p.edit().remove(K_UNKNOWN).remove(K_UNKNOWN_AT).apply()
        }
        val kosher = if (r.provider != null) true else if (r.reached) false else null
        if (kosher == false) {
            plainHandle = current
            plainAt = now
        }
        val works = kosher == true && active == null && tunnelWorks()
        when (decide(true, active, current, kosher, works)) {
            Step.ENTER -> enter(app, r.provider!!, current)
            Step.EXIT -> exit(app, "network is no longer ${p.getString(K_PROVIDER, "")}")
            Step.STAY -> p.edit().putLong(K_VERIFIED, now).apply()
            Step.NONE -> if (kosher == true) Log.i(TAG, "kosher network (${r.provider}) — our tunnel works here too, staying on it")
        }
    }

    /** ה-VPN שלנו עובד גם ברשת הזו (יש ספקים שמתירים) — אז עדיף להישאר עליו. */
    private fun tunnelWorks(): Boolean {
        if (TunnelManager.state != com.wireguard.android.backend.Tunnel.State.UP) return false
        val age = TunnelManager.handshakeAgeMs() ?: return false
        if (age > 3 * 60_000L) return false
        return DataProbe.run(listOf(DataProbe.TARGETS.first())) == null
    }

    private fun enter(context: Context, provider: String, handle: Long) {
        val deviceOwner = DeviceOwnerManager.isDeviceOwner(context)
        if (!deviceOwner && TunnelManager.lockdown() == true) {
            // בלי מנהל מכשיר אי אפשר לכבות את "חסימת חיבורים ללא VPN" שהמשתמש הגדיר — רק להסביר
            notifyLockdown(context, provider, handle)
            return
        }
        prefs(context).edit().putString(K_PROVIDER, provider).putLong(K_HANDLE, handle)
            .putLong(K_SINCE, System.currentTimeMillis()).putLong(K_VERIFIED, System.currentTimeMillis()).commit()
        Log.i(TAG, "kosher network detected ($provider) — pausing our VPN on this network only")
        if (deviceOwner) {
            installCerts(context, provider)
            runCatching { DeviceOwnerManager.apply(context, GuardPolicy.get(context)) }   // "תמיד מופעל" כבוי ברשת הזו
                .onFailure { Log.w(TAG, "device owner apply (kosher enter) failed", it) }
        }
        TunnelManager.healthCheck()                 // kosherGate — המנהרה יורדת (הרצון לחיבור נשמר)
        watch(context, true)
        ProtectionService.refresh(context)
        Recovery.syncSoon(context)                  // הדשבורד: "ברשת נטפרי"
    }

    private fun exit(context: Context, why: String) {
        val p = prefs(context)
        val was = p.getString(K_PROVIDER, null)
        p.edit().remove(K_PROVIDER).remove(K_HANDLE).remove(K_SINCE).remove(K_VERIFIED).commit()
        watch(context, false)
        Log.i(TAG, "left the kosher network ($was): $why — VPN back")
        // קודם הנעילה (בלי VPN אין רשת לאפליקציות), ואז המנהרה
        runCatching { DeviceOwnerManager.apply(context, GuardPolicy.get(context)) }
            .onFailure { Log.w(TAG, "device owner apply (kosher exit) failed", it) }
        TunnelManager.ensureConnected("kosher exit")
        ProtectionService.refresh(context)
        Recovery.syncSoon(context)
    }

    /** כשבמצב כשר: אנדרואיד מעיר אותנו על כל רשת חדשה — גם אם התהליך מת בינתיים. */
    private fun watch(context: Context, on: Boolean) {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or
            (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0)
        val pi = PendingIntent.getBroadcast(context, WATCH_REQUEST, Intent(context, KosherNetReceiver::class.java), flags)
        runCatching { cm.unregisterNetworkCallback(pi) }
        if (on) {
            runCatching {
                cm.registerNetworkCallback(NetworkRequest.Builder()
                    .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET).build(), pi)
            }.onFailure { Log.w(TAG, "kosher network watch not registered", it) }
        }
    }

    // ---------------------------------------------------------------- התקנת התעודות (מנהל מכשיר)

    /** מתקין בשקט את כל תעודות הספק (כדי שהגלישה המאובטחת ב-Chrome תעבוד ברשת שלו). */
    private fun installCerts(context: Context, provider: String) {
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
        val admin = DeviceAdmin.component(context)
        val p = prefs(context)
        val installed = p.getStringSet(K_INSTALLED, emptySet())!!.toMutableSet()
        for (c in providers(context).firstOrNull { it.id == provider }?.roots.orEmpty()) {
            val fp = fingerprint(c)
            runCatching {
                if (!dpm.hasCaCertInstalled(admin, c.encoded) && dpm.installCaCert(admin, c.encoded)) installed += fp
            }.onFailure { Log.w(TAG, "kosher certificate install failed", it) }
        }
        p.edit().putStringSet(K_INSTALLED, installed).apply()
    }

    /** ההגדרה כובתה מהדשבורד: מסירים את התעודות שאנחנו התקנו (ורק אותן). */
    private fun cleanupCerts(context: Context) {
        val p = prefs(context)
        val installed = p.getStringSet(K_INSTALLED, emptySet())!!
        if (installed.isEmpty() || !DeviceOwnerManager.isDeviceOwner(context)) return
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return
        val admin = DeviceAdmin.component(context)
        for (c in providers(context).flatMap { it.roots }) {
            if (fingerprint(c) in installed) runCatching { dpm.uninstallCaCert(admin, c.encoded) }
        }
        p.edit().remove(K_INSTALLED).apply()
    }

    /** SSL לבקשות שלנו לשרת (Kotlin): תעודות המערכת + של הספקים הכשרים — ברשת כשרה הם פותחים HTTPS. */
    fun socketFactory(context: Context): SSLSocketFactory {
        val app = context.applicationContext
        val system = systemTrustManager()
        val tm = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
                system.checkClientTrusted(chain, authType)

            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                try {
                    system.checkServerTrusted(chain, authType)
                } catch (e: CertificateException) {
                    signedBy(chain, authType, providers(app)) ?: throw e
                }
            }

            override fun getAcceptedIssuers(): Array<X509Certificate> = system.acceptedIssuers
        }
        return SSLContext.getInstance("TLS").apply { init(null, arrayOf(tm), null) }.socketFactory
    }

    // ---------------------------------------------------------------- התראה (בלי מנהל מכשיר)

    private fun notifyLockdown(context: Context, provider: String, handle: Long) {
        val p = prefs(context)
        if (p.getLong(K_NOTICE_HANDLE, 0L) == handle) return          // פעם אחת לכל רשת
        p.edit().putLong(K_NOTICE_HANDLE, handle).apply()
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CHANNEL_ID) == null) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL_ID, context.getString(R.string.alerts_channel),
                NotificationManager.IMPORTANCE_HIGH))
        }
        val name = LABELS[provider] ?: LABELS.getValue("other")
        val open = context.packageManager.getLaunchIntentForPackage(context.packageName)
        val pi = open?.let {
            PendingIntent.getActivity(context, NOTICE_ID, it, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        }
        runCatching {
            nm.notify(NOTICE_ID, Notification.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_shield)
                .setContentTitle(context.getString(R.string.kosher_lockdown_title, name))
                .setStyle(Notification.BigTextStyle().bigText(context.getString(R.string.kosher_lockdown_text, name)))
                .setContentText(context.getString(R.string.kosher_lockdown_text, name))
                .apply { if (pi != null) setContentIntent(pi) }
                .build())
        }
    }
}

/** אנדרואיד מעיר אותנו על רשת חדשה (רק כשבמצב כשר — KosherNet.watch). */
class KosherNetReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val pending = goAsync()
        KosherNet.evaluateAsync(context, "watch") { pending.finish() }
    }
}
