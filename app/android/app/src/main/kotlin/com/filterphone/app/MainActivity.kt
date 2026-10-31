package com.filterphone.app

import android.Manifest
import android.app.ActivityManager
import android.app.NotificationManager
import android.app.admin.DevicePolicyManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.net.VpnService
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import com.wireguard.android.backend.Tunnel
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.util.concurrent.Executors

/**
 * הגשר בין ממשק ה-Flutter לקוד האנדרואיד.
 *   filterphone/native     — פקודות (מפתחות, VPN, תעודה, מגן, סוללה, מנהל מכשיר, סנכרון)
 *   filterphone/vpn_state  — עדכוני מצב המנהרה בזמן אמת
 */
class MainActivity : FlutterActivity() {
    private val main = Handler(Looper.getMainLooper())
    private var pendingVpn: MethodChannel.Result? = null
    private var launchingChild = false     // פתחנו מסך של אנדרואיד — יציאה כזו לא מסירה מהאחרונים
    private var pendingCert: MethodChannel.Result? = null
    private var pendingAvatar: MethodChannel.Result? = null
    private var vpnListener: ((Tunnel.State) -> Unit)? = null
    private var installProgress: InstalledApps.Progress? = null
    /** פתיחה מהתראה ("יש תשובה מהתמיכה") — ה-Flutter שואל פעם אחת ועובר ללשונית. */
    private var launchTarget: String? = null

    override fun onCreate(savedInstanceState: android.os.Bundle?) {
        launchTarget = intent?.getStringExtra(SupportNotify.EXTRA_OPEN)
        super.onCreate(savedInstanceState)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        intent.getStringExtra(SupportNotify.EXTRA_OPEN)?.let { launchTarget = it }
    }

    override fun onResume() {
        super.onResume()
        launchingChild = false        // חזרנו לחזית — אם נצא עכשיו ליישומים אחרונים, זו יציאה אמיתית
    }

    /**
     * המשתמש יצא ליישומים האחרונים / מסך הבית — מסירים את FilterPhone מהרשימה כדי שלא יהיה אפשר
     * להחליק ולסגור אותו (ההגנה ממשיכה בשירות הקדמי). רק אחרי ההקמה (יש כבר הגדרות), ולא כשאנחנו
     * עצמנו פתחנו מסך של אנדרואיד (הרשאה / הגדרות) — אז צריך לחזור לאפליקציה כרגיל.
     */
    override fun onUserLeaveHint() {
        super.onUserLeaveHint()
        if (!launchingChild && GuardPolicy.credentials(this) != null) {
            runCatching { finishAndRemoveTask() }
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        TunnelManager.init(this)
        val messenger = flutterEngine.dartExecutor.binaryMessenger
        MethodChannel(messenger, "filterphone/native").setMethodCallHandler(::onCall)
        EventChannel(messenger, "filterphone/vpn_state").setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(args: Any?, sink: EventChannel.EventSink) {
                detachVpnListener()
                val l: (Tunnel.State) -> Unit = { s -> main.post { runCatching { sink.success(s.name) } } }
                vpnListener = l
                TunnelManager.addListener(l)
                sink.success(TunnelManager.state.name)
            }
            override fun onCancel(args: Any?) = detachVpnListener()
        })
        // טאב העדכונים: התקדמות ההתקנה (גם של Google Play) בזמן אמת
        EventChannel(messenger, "filterphone/install_progress").setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(args: Any?, sink: EventChannel.EventSink) {
                installProgress?.unregister()
                installProgress = InstalledApps.Progress(this@MainActivity) { e -> runCatching { sink.success(e) } }
                    .also { it.register() }
            }
            override fun onCancel(args: Any?) {
                installProgress?.unregister()
                installProgress = null
            }
        })
    }

    /** המאזין מחזיק הפניה למסך — חייב לצאת כשהמסך נהרס (אחרת דליפת זיכרון). */
    private fun detachVpnListener() {
        vpnListener?.let { TunnelManager.removeListener(it) }
        vpnListener = null
    }

    override fun cleanUpFlutterEngine(flutterEngine: FlutterEngine) {
        detachVpnListener()
        super.cleanUpFlutterEngine(flutterEngine)
    }

    override fun onDestroy() {
        detachVpnListener()
        installProgress?.unregister()
        installProgress = null
        super.onDestroy()
    }

    private fun onCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "deviceInfo" -> result.success(mapOf(
                "model" to Build.MODEL,
                "manufacturer" to Build.MANUFACTURER,
                "sdk" to Build.VERSION.SDK_INT,
                "appVersion" to packageManager.getPackageInfo(packageName, 0).versionName,
            ))
            "deviceUid" -> result.success(DeviceId.get(this))
            "androidId" -> result.success(DeviceId.androidId(this))
            "generateKeyPair" -> background(result) { TunnelManager.newKeyPair() }
            "publicKey" -> background(result) { TunnelManager.publicKey() }
            "saveVpnConfig" -> background(result) {
                TunnelManager.saveConfig(call.argument<String>("vpn")!!)
                // מנהל מכשיר: עכשיו שיש הגדרות — אפשר לנעול "VPN תמיד מופעל"
                runCatching { DeviceOwnerManager.apply(this, GuardPolicy.get(this)) }
                true
            }
            "prepareVpn" -> {
                val intent = VpnService.prepare(this)
                if (intent == null) {
                    result.success(true)
                } else {
                    pendingVpn?.success(false)      // בקשה קודמת שלא נענתה — לא משאירים אותה תלויה
                    pendingVpn = result
                    try {
                        launchingChild = true
                        startActivityForResult(intent, REQ_VPN)
                    } catch (e: ActivityNotFoundException) {
                        pendingVpn = null
                        result.error("NoVpnDialog", e.message, null)
                    }
                }
            }
            "connect" -> background(result) {
                val state = TunnelManager.connect().get().name
                Recovery.scheduleDaily(this)
                main.post { Recovery.startProtection(this) }
                state
            }
            "disconnect" -> background(result) { TunnelManager.disconnect().get().name }
            "vpnStatus" -> background(result) { TunnelManager.status() }
            "isCaInstalled" -> background(result) {
                val pem = call.argument<String>("pem")!!
                ProtectionState.setString(this, ProtectionState.CA_PEM, pem)   // לדיווח מהרקע
                CertHelper.isInstalled(pem)
            }
            "installCa" -> {
                launchingChild = true          // פותח מסך התקנת תעודה — לא יציאה אמיתית
                val mode = CertHelper.install(this, call.argument<String>("pem")!!, REQ_CERT)
                if (mode == "dialog") pendingCert = result else result.success(mode)
            }
            "openVpnSettings" -> {
                startSettings(Intent(Settings.ACTION_VPN_SETTINGS))
                result.success(true)
            }
            "privateDnsMode" -> result.success(Settings.Global.getString(contentResolver, "private_dns_mode") ?: "off")
            "isDeviceOwner" -> result.success(DeviceOwnerManager.isDeviceOwner(this))
            "adminPauseStatus" -> result.success(mapOf(
                "active" to AdminState.isPaused(this),
                "until" to AdminState.pauseUntil(this),
            ))
            // "החזר הגנה" בזמן השהיה: הכול חוזר מיד (VPN, מגן, מנהל מכשיר), וגם בשרת ההשהיה נגמרת
            "resumeProtectionLocally" -> background(result) {
                AdminState.dismissCurrentPause(this)
                Recovery.onPauseChanged(this)
                runCatching { Sync.notify(this, "/v1/device/resume") }
                Recovery.syncSoon(this)
                true
            }
            // "הסרת הכול" — רק בהשהיה שהמנהל אישר (נבדק כאן, לא רק בממשק). אחר כך: requestSelfUninstall
            "uninstallEverything", "removeManagementLocally" -> background(result) { Uninstall.everything(this) }
            "requestSelfUninstall" -> {
                val intent = Intent(Intent.ACTION_DELETE, Uri.parse("package:$packageName"))
                result.success(tryStart(intent))
            }
            "preflight" -> background(result) { Preflight.report(this) }
            // הקמה בלחיצה אחת: מה שאפשר בלי מגע (root / מנהל מכשיר); השאר — הממשק מוביל
            "setupAll" -> background(result) {
                val report = OneTapSetup.run(this, call.argument<String>("ca"))
                main.post { Recovery.startProtection(this) }
                report
            }
            "provisionRoot" -> background(result) { RootProvisioner.provision(this).toMap() }
            // ניפוי אלחוטי מתוך האפליקציה (בלי מחשב, בלי Shizuku): נתמך? ופתיחת אפשרויות מפתחים.
            "wirelessSupported" -> result.success(WirelessSetup.isSupported())
            "openDeveloperOptions" -> result.success(WirelessSetup.openDeveloperOptions(this))
            // מסך ההתקנה המודרך: מה המצב בטלפון, ופתיחת המסך הנכון בהגדרות (המשתמש לוחץ ומאשר בעצמו)
            "setupStatus" -> result.success(SetupGuide.status(this))
            "openSetupPage" -> result.success(SetupGuide.open(this, call.argument<String>("page").orEmpty()))
            "lastSetupReport" -> result.success(SetupGuide.lastReport(this))
            // התראת הזנת קוד ההתאמה — מקלידים את הקוד בשורת ההתראה בלי לצאת ממסך "ניפוי אלחוטי"
            "startWirelessNotification" -> result.success(WirelessNotify.prompt(this, call.argument<String>("ca")))
            // ההקמה בניפוי אלחוטי: מקבל קוד התאמה (6 ספרות) ופורט (0 = לגלות ב-mDNS), ומבצע הכול.
            "provisionWireless" -> background(result) {
                val report = OneTapSetup.runWireless(this,
                    call.argument<String>("code").orEmpty(),
                    call.argument<Int>("port") ?: 0,
                    call.argument<String>("ca"))
                main.post { Recovery.startProtection(this) }
                report
            }
            "reset" -> background(result) {
                AdminState.clear(this)
                TunnelManager.reset()
                GuardPolicy.clear(this)
                Recovery.cancelAll(this)
                stopService(Intent(this, ProtectionService::class.java))
                true
            }
            // מגן FilterPhone — חסימת אפליקציות ומסכים בטלפון
            "installedApps" -> background(result) { GuardPolicy.installedApps(this) }
            // טאב העדכונים: כל האפליקציות בטלפון (גרסה, מתי עודכנה, אייקון)
            "installedForUpdates" -> background(result) { InstalledApps.forUpdates(this) }
            // החנות: התקנה/עדכון דרך מנהל המכשיר, מצב לכל אפליקציה, ופתיחה
            "installStoreApp" -> background(result) {
                ManagedApps.installNow(this, org.json.JSONObject(call.argument<String>("item") ?: "{}"))
            }
            "storeAppStates" -> background(result) {
                (call.argument<List<String>>("packages") ?: emptyList()).associateWith { pkg ->
                    mapOf("installed_version" to ManagedApps.installedVersion(this, pkg),
                        "state" to ManagedApps.state(this, pkg))
                }
            }
            "openApp" -> {
                val launch = call.argument<String>("package")?.let { packageManager.getLaunchIntentForPackage(it) }
                result.success(launch != null && tryStart(launch))
            }
            // הסרה מהחנות: במנהל מכשיר — שקט; אחרת — חלון ההסרה של אנדרואיד. לא מסירים את FilterPhone עצמה.
            "uninstallApp" -> {
                val pkg = call.argument<String>("package").orEmpty()
                when {
                    pkg.isBlank() || pkg == packageName -> result.success("invalid")
                    DeviceOwnerManager.isDeviceOwner(this) -> background(result) { ManagedApps.uninstall(this, pkg) }
                    else -> result.success(
                        if (tryStart(Intent(Intent.ACTION_DELETE, Uri.parse("package:$pkg")))) "dialog" else "error")
                }
            }
            // מתווך ל-Google Play: פותח את העמוד של האפליקציה הספציפית ב-Play (גם כשה-Play מוסתר לכניסה).
            "openPlay" -> {
                val pkg = call.argument<String>("package").orEmpty()
                val url = call.argument<String>("url")
                // שלב א': כש-Play חסום לכניסה — "חלון התקנה" לאפליקציה הזו בלבד (GuardService/PlayLock)
                if (pkg.isNotBlank()) {
                    PlayLock.start(pkg, call.argument<List<String>>("titles").orEmpty(),
                        ManagedApps.installedVersion(this, pkg), SystemClock.elapsedRealtime())
                    GuardService.onPlaySession()
                }
                val market = Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=$pkg"))
                    .setPackage("com.android.vending")
                val web = Uri.parse(url?.takeIf { it.startsWith("https://") }
                    ?: "https://play.google.com/store/apps/details?id=$pkg")
                result.success(tryStart(market) || tryStart(Intent(Intent.ACTION_VIEW, web)))
            }
            "isGuardEnabled" -> result.success(GuardPolicy.isServiceEnabled(this))
            "openGuardSettings" -> {
                startSettings(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                result.success(true)
            }
            "setGuardPolicy" -> background(result) {
                val ok = GuardPolicy.save(this, call.argument<String>("policy") ?: "{}")
                // בלי האסימון המגן עדיין אוכף — רק לא מתעדכן לבד כשהאפליקציה סגורה
                runCatching {
                    GuardPolicy.setCredentials(this, call.argument<String>("api"), call.argument<String>("token"))
                }.onFailure { Log.w("FilterPhone", "guard credentials not saved", it) }
                if (ok) Recovery.syncSoon(this)      // אישור הגרסה לשרת
                ok
            }
            "setCredentials" -> background(result) {
                GuardPolicy.setCredentials(this, call.argument<String>("api"), call.argument<String>("token"))
                Recovery.scheduleDaily(this)
                true
            }
            // מצב ההגנה המלא — למסך הראשי
            "protectionStatus" -> background(result) { protectionStatus() }
            "syncNow" -> background(result) { Sync.heartbeat(this) }
            "startProtection" -> result.success(Recovery.startProtection(this))
            // גרסה חדשה בלי מנהל מכשיר: דף ההורדה בדפדפן (המשתמש מתקין בעצמו). באנדרואיד 16 — התקנה
            // מתוך האפליקציה, כדי להוריד את ה-VPN רגע לפני (באג של אנדרואיד 16 — TunnelManager.pauseForUpdate).
            // תשובה: page / installing / permission (לאשר "התקנת אפליקציות לא מוכרות") / קוד שגיאה
            "openUpdatePage" -> {
                if (SelfUpdate.installInApp(this)) {
                    if (!packageManager.canRequestPackageInstalls()) {
                        val settings = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName"))
                        result.success(if (tryStart(settings)) "permission" else "error")
                    } else {
                        background(result) { SelfUpdate.installRequested(this) ?: "installing" }
                    }
                } else {
                    val page = SelfUpdate.available(this)?.get("page")
                    result.success(if (page != null && tryStart(Intent(Intent.ACTION_VIEW, Uri.parse(page)))) "page" else "error")
                }
            }
            "launchTarget" -> { result.success(launchTarget); launchTarget = null }
            // הסייר המוגן: המצב (full / essential / off), האתרים החיוניים, והאם הוא דפדפן ברירת המחדל
            "safeBrowser" -> result.success(mapOf(
                "mode" to BrowserActivity.currentMode(this),
                "isDefault" to BrowserActivity.isDefault(this),
                "sites" to SafeSites.SITES.map { mapOf("group" to it.group, "title" to it.title, "url" to it.url) },
            ))
            "openSafeBrowser" -> {
                launchingChild = true          // הסייר נפתח מעל — חזרה ממנו מגיעה לכאן
                result.success(runCatching {
                    BrowserActivity.open(this, call.argument<String>("url"), call.argument<String>("query"))
                    true
                }.getOrDefault(false))
            }
            // תמונת פרופיל: בוחר התמונות → JPEG ריבועי מוקטן (base64), או null אם בוטל
            "pickAvatar" -> {
                pendingAvatar?.success(null)
                pendingAvatar = result
                launchingChild = true
                if (!AvatarPicker.launch(this, REQ_AVATAR)) {
                    pendingAvatar = null
                    result.success(null)
                }
            }
            "requestDefaultBrowser" -> {
                launchingChild = true
                result.success(BrowserActivity.requestDefault(this, REQ_BROWSER))
            }
            "clearSupportNotification" -> { SupportNotify.clear(this); result.success(true) }
            // תמיכה — פתיחת שיחת WhatsApp עם פרטי מכשיר/מצב שנמסרו מה-Flutter
            "openSupportWhatsApp" -> {
                val number = call.argument<String>("number")
                    ?.filter(Char::isDigit)
                    .orEmpty()
                val message = call.argument<String>("message").orEmpty()
                if (number.isBlank()) {
                    result.error("SupportNumberMissing", "מספר התמיכה לא הוגדר", null)
                    return
                }
                val uri = Uri.parse("https://wa.me/$number?text=${Uri.encode(message)}")
                if (tryStart(Intent(Intent.ACTION_VIEW, uri))) {
                    result.success(true)
                } else {
                    result.error("WhatsAppUnavailable", "לא ניתן לפתוח את WhatsApp במכשיר", null)
                }
            }
            // שיתוף FilterPhone עם חבר — חלון השיתוף של אנדרואיד
            "shareText" -> {
                val text = call.argument<String>("text").orEmpty()
                val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
                result.success(tryStart(Intent.createChooser(send, null).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)))
            }
            // סוללה
            "requestBatteryExemption" -> result.success(requestBatteryExemption())
            // התראות (אנדרואיד 13+): ההתראה "הסינון פעיל"
            "requestNotifications" -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                    checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                    requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFY)
                }
                result.success(true)
            }
            // מנהל מכשיר רגיל (לא Device Owner): רק מקשה על הסרה ומדווח כשמבטלים
            "requestDeviceAdmin" -> {
                val intent = Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN)
                    .putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, DeviceAdmin.component(this))
                    .putExtra(DevicePolicyManager.EXTRA_ADD_EXPLANATION, getString(R.string.admin_add_explanation))
                result.success(tryStart(intent))
            }
            // הקמה כ-Device Owner ב-QR: קוד ההפעלה שהגיע בתוך ה-QR
            "provisioningInfo" -> result.success(Provisioning.pending(this))
            "clearProvisioning" -> { Provisioning.clear(this); result.success(true) }
            else -> result.notImplemented()
        }
    }

    private fun protectionStatus(): Map<String, Any?> {
        val power = getSystemService(PowerManager::class.java)
        val am = getSystemService(ActivityManager::class.java)
        val policy = GuardPolicy.get(this)
        return mapOf(
            "batteryUnrestricted" to (power?.isIgnoringBatteryOptimizations(packageName) ?: false),
            "backgroundRestricted" to (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) am?.isBackgroundRestricted else null),
            "notifications" to getSystemService(NotificationManager::class.java)?.areNotificationsEnabled(),
            "guardEnabled" to GuardPolicy.isServiceEnabled(this),
            "guardRunning" to GuardService.isRunning,
            "deviceOwner" to DeviceOwnerManager.isDeviceOwner(this),
            "deviceAdmin" to DeviceOwnerManager.isAdminActive(this),
            "policyVersion" to policy.version,
            "lastPolicySyncMs" to ProtectionState.time(this, ProtectionState.POLICY_SYNC),
            "lastVpnHealthOkMs" to ProtectionState.time(this, ProtectionState.VPN_HEALTH_OK),
            "lastHeartbeatMs" to ProtectionState.time(this, ProtectionState.HEARTBEAT_OK),
            "heartbeatError" to ProtectionState.string(this, ProtectionState.HEARTBEAT_ERROR),
            "policyError" to GuardPolicy.lastError(this),
            "updateAvailable" to SelfUpdate.available(this)?.get("version"),
        )
    }

    /**
     * פטור מאופטימיזציית סוללה: חלון המערכת הרשמי ("לאפשר ל-FilterPhone לפעול ברקע?").
     * אם אין כזה ביצרן — רשימת האפליקציות בהגדרות הסוללה, ואם גם זה לא קיים — דף האפליקציה.
     * מחזיר מה נפתח. אנחנו לא "מכבים" כלום בעצמנו — רק המשתמש יכול לאשר.
     */
    private fun requestBatteryExemption(): String {
        val power = getSystemService(PowerManager::class.java)
        if (power?.isIgnoringBatteryOptimizations(packageName) == true) {
            // כבר פטור; אם יש "הגבלת רקע" (אנדרואיד 9+) — רק בדף האפליקציה אפשר לבטל
            val am = getSystemService(ActivityManager::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P && am?.isBackgroundRestricted == true) {
                startSettings(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
                return "app_details"
            }
            return "already"
        }
        @Suppress("BatteryLife")
        val direct = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName"))
        if (tryStart(direct)) return "dialog"
        if (tryStart(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))) return "list"
        startSettings(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
        return "app_details"
    }

    /** פותח מסך; אם אין כזה ביצרן — false (בלי חלופה). */
    private fun tryStart(intent: Intent): Boolean = try {
        launchingChild = true          // אנחנו פותחים מסך — יציאה כזו לא מסירה את האפליקציה מהאחרונים
        startActivity(intent)
        true
    } catch (e: ActivityNotFoundException) {
        false
    } catch (e: SecurityException) {
        false
    }

    /** פותח מסך הגדרות; אם אין כזה — את ההגדרות הראשיות. */
    private fun startSettings(intent: Intent): Boolean = tryStart(intent) || tryStart(Intent(Settings.ACTION_SETTINGS)).let { false }

    /** פעולות שעלולות לחסום (קריפטו, רשת, דיסק) רצות ברקע; התשובה חוזרת ב-main thread. */
    private fun background(result: MethodChannel.Result, block: () -> Any?) {
        io.execute {
            try {
                val value = block()
                main.post { result.success(value) }
            } catch (e: Exception) {
                main.post { result.error(e.javaClass.simpleName, e.message ?: e.cause?.message, null) }
            }
        }
    }

    @Deprecated("FlutterActivity עדיין מעביר תוצאות דרך המתודה הזו")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        when (requestCode) {
            REQ_VPN -> { pendingVpn?.success(resultCode == RESULT_OK); pendingVpn = null }
            REQ_CERT -> { pendingCert?.success(if (resultCode == RESULT_OK) "installed" else "dialog"); pendingCert = null }
            REQ_AVATAR -> {
                val pending = pendingAvatar ?: return
                pendingAvatar = null
                val uri = data?.data
                if (resultCode != RESULT_OK || uri == null) pending.success(null)
                else background(pending) { AvatarPicker.toJpegBase64(this, uri) }
            }
        }
    }

    private companion object {
        const val REQ_VPN = 4101
        const val REQ_CERT = 4102
        const val REQ_NOTIFY = 4103
        const val REQ_BROWSER = 4104
        const val REQ_AVATAR = 4105

        /** חוט רקע אחד לכל האפליקציה — לא חדש בכל יצירה של המסך (היה דולף). */
        val io = Executors.newSingleThreadExecutor { r -> Thread(r, "fp-bridge").apply { isDaemon = true } }
    }
}
