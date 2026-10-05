package com.filterphone.app

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.VpnService
import android.os.Build
import android.os.Handler
import android.os.Looper
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
 *   filterphone/native     — פקודות (מפתחות, VPN, תעודה, מידע על המכשיר)
 *   filterphone/vpn_state  — עדכוני מצב המנהרה בזמן אמת
 */
class MainActivity : FlutterActivity() {
    private val main = Handler(Looper.getMainLooper())
    private val io = Executors.newSingleThreadExecutor()
    private var pendingVpn: MethodChannel.Result? = null
    private var pendingCert: MethodChannel.Result? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        TunnelManager.init(this)
        val messenger = flutterEngine.dartExecutor.binaryMessenger
        MethodChannel(messenger, "filterphone/native").setMethodCallHandler(::onCall)
        EventChannel(messenger, "filterphone/vpn_state").setStreamHandler(object : EventChannel.StreamHandler {
            private var listener: ((Tunnel.State) -> Unit)? = null
            override fun onListen(args: Any?, sink: EventChannel.EventSink) {
                val l: (Tunnel.State) -> Unit = { s -> main.post { sink.success(s.name) } }
                listener = l
                TunnelManager.addListener(l)
                sink.success(TunnelManager.state.name)
            }
            override fun onCancel(args: Any?) {
                listener?.let { TunnelManager.removeListener(it) }
                listener = null
            }
        })
    }

    private fun onCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "deviceInfo" -> result.success(mapOf(
                "model" to Build.MODEL,
                "manufacturer" to Build.MANUFACTURER,
                "sdk" to Build.VERSION.SDK_INT,
                "appVersion" to packageManager.getPackageInfo(packageName, 0).versionName,
            ))
            "generateKeyPair" -> background(result) { TunnelManager.newKeyPair() }
            "publicKey" -> background(result) { TunnelManager.publicKey() }
            "saveVpnConfig" -> background(result) { TunnelManager.saveConfig(call.argument<String>("vpn")!!); true }
            "prepareVpn" -> {
                val intent = VpnService.prepare(this)
                if (intent == null) {
                    result.success(true)
                } else {
                    pendingVpn = result
                    startActivityForResult(intent, REQ_VPN)
                }
            }
            "connect" -> background(result) { TunnelManager.connect().get().name }
            "disconnect" -> background(result) { TunnelManager.disconnect().get().name }
            "vpnStatus" -> background(result) { TunnelManager.status() }
            "isCaInstalled" -> background(result) { CertHelper.isInstalled(call.argument<String>("pem")!!) }
            "installCa" -> {
                val mode = CertHelper.install(this, call.argument<String>("pem")!!, REQ_CERT)
                if (mode == "dialog") pendingCert = result else result.success(mode)
            }
            "openVpnSettings" -> {
                startActivity(Intent(Settings.ACTION_VPN_SETTINGS))
                result.success(true)
            }
            "privateDnsMode" -> result.success(Settings.Global.getString(contentResolver, "private_dns_mode") ?: "off")
            "isDeviceOwner" -> result.success(DeviceAdmin.isDeviceOwner(this))
            "reset" -> background(result) { TunnelManager.reset(); true }
            // מגן FilterPhone — חסימת אפליקציות ומסכים בטלפון
            "installedApps" -> background(result) { GuardPolicy.installedApps(this) }
            "isGuardEnabled" -> result.success(GuardPolicy.isServiceEnabled(this))
            "openGuardSettings" -> {
                try {
                    startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                } catch (e: ActivityNotFoundException) {
                    startActivity(Intent(Settings.ACTION_SETTINGS))
                }
                result.success(true)
            }
            "setGuardPolicy" -> background(result) {
                GuardPolicy.save(this, call.argument<String>("policy") ?: "{}")
                // בלי האסימון המגן עדיין אוכף — רק לא מתעדכן לבד כשהאפליקציה סגורה
                runCatching {
                    GuardPolicy.setCredentials(this, call.argument<String>("api"), call.argument<String>("token"))
                }.onFailure { Log.w("FilterPhone", "guard credentials not saved", it) }
                true
            }
            else -> result.notImplemented()
        }
    }

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
        }
    }

    private companion object {
        const val REQ_VPN = 4101
        const val REQ_CERT = 4102
    }
}
