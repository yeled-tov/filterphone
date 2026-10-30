package com.filterphone.app

import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/**
 * מסך ההתקנה המודרך (InstallScreen): מה המצב עכשיו בטלפון — כדי שהמסך יוביל צעד-צעד ויתקדם לבד
 * כשהמשתמש חוזר מההגדרות — ופתיחת המסך הנכון בהגדרות. המשתמש עושה בעצמו כל לחיצה ואישור באנדרואיד;
 * כאן רק בודקים ופותחים.
 *
 *   status()   — יצרן/גרסה, אפשרויות מפתחים, ניפוי USB/אלחוטי, Wi-Fi, התראות, מנהל מכשיר.
 *   open(page) — about (אודות הטלפון — 7 לחיצות על מספר Build), developer, wireless (אפשרויות מפתחים
 *                עם סימון "ניפוי באגים אלחוטי"), wifi.
 *   הדוח האחרון של ההקמה האלחוטית (גם כשהקוד הוקלד בהתראה) — נשמר, כדי שהמסך יסביר מה חסם.
 */
object SetupGuide {
    private const val TAG = "FilterPhone"
    private const val REPORT = "wireless_report"

    /** משפחת היצרן — לפיה ההסבר איפה נמצא "מספר Build". טהור. */
    fun vendor(manufacturer: String, brand: String): String {
        val m = "$manufacturer $brand".lowercase()
        return when {
            "samsung" in m -> "samsung"
            "xiaomi" in m || "redmi" in m || "poco" in m -> "xiaomi"
            "oneplus" in m || "oppo" in m || "realme" in m -> "oneplus"
            "google" in m -> "pixel"
            else -> "other"
        }
    }

    fun status(context: Context): Map<String, Any?> {
        val cr = context.contentResolver
        fun global(name: String): Boolean? = runCatching { Settings.Global.getInt(cr, name) == 1 }.getOrNull()
        return mapOf(
            "sdk" to Build.VERSION.SDK_INT,
            "manufacturer" to Build.MANUFACTURER,
            "brand" to Build.BRAND,
            "model" to Build.MODEL,
            "vendor" to vendor(Build.MANUFACTURER.orEmpty(), Build.BRAND.orEmpty()),
            "dev_options" to (global(Settings.Global.DEVELOPMENT_SETTINGS_ENABLED) ?: false),
            "adb" to (global(Settings.Global.ADB_ENABLED) ?: false),
            "adb_wifi" to global("adb_wifi_enabled"),          // באנדרואיד חדש אולי לא קריא — null
            "wifi" to wifiConnected(context),
            "notifications" to (context.getSystemService(NotificationManager::class.java)?.areNotificationsEnabled() == true),
            "device_owner" to DeviceOwnerManager.isDeviceOwner(context),
            "wireless_supported" to WirelessSetup.isSupported(),
        )
    }

    @Suppress("DEPRECATION")        // allNetworks: גם רשת ה-VPN שלנו פעילה — מחפשים Wi-Fi מתחתיה
    private fun wifiConnected(context: Context): Boolean {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return false
        return runCatching {
            cm.allNetworks.any { n ->
                cm.getNetworkCapabilities(n)?.let {
                    it.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) &&
                        it.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                } == true
            }
        }.getOrDefault(false)
    }

    /** פותח מסך בהגדרות. false אם לא נפתח. */
    fun open(context: Context, page: String): Boolean {
        val intent = when (page) {
            "about" -> Intent(Settings.ACTION_DEVICE_INFO_SETTINGS)
            "developer" -> Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)
            // אותו מסך, עם סימון השורה "ניפוי באגים אלחוטי" (מה שההגדרות עושות כשמחפשים בהן)
            "wireless" -> Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS).apply {
                putExtra(":settings:fragment_args_key", "toggle_adb_wireless")
                putExtra(":settings:show_fragment_args", Bundle().apply {
                    putString(":settings:fragment_args_key", "toggle_adb_wireless")
                })
            }
            "wifi" -> Intent(Settings.ACTION_WIFI_SETTINGS)
            "settings" -> Intent(Settings.ACTION_SETTINGS)
            else -> return false
        }.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            context.startActivity(intent)
            true
        } catch (e: Exception) {
            Log.w(TAG, "could not open $page settings", e)
            // יצרן שחסם את המסך הישיר — המסך שמעליו (משם ההסבר במסך ההתקנה מוביל)
            val parent = mapOf("wireless" to "developer", "developer" to "settings", "about" to "settings",
                "wifi" to "settings")[page]
            parent != null && open(context, parent)
        }
    }

    // ------------------------------------------------------------ הדוח האחרון

    fun saveReport(context: Context, report: Map<String, Any?>) {
        val o = JSONObject()
        for ((k, v) in report) {
            when (v) {
                is List<*> -> o.put(k, JSONArray(v.map { it.toString() }))
                is Boolean, is Number, is String -> o.put(k, v)
                null -> {}
                else -> o.put(k, v.toString())
            }
        }
        o.put("at", System.currentTimeMillis())
        ProtectionState.setString(context, REPORT, o.toString())
    }

    fun lastReport(context: Context): Map<String, Any?>? {
        val raw = ProtectionState.string(context, REPORT) ?: return null
        return runCatching {
            val o = JSONObject(raw)
            o.keys().asSequence().associateWith { k ->
                when (val v = o.get(k)) {
                    is JSONArray -> (0 until v.length()).map { v.getString(it) }
                    else -> v
                }
            }
        }.getOrNull()
    }
}
