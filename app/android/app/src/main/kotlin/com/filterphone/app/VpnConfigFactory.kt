package com.filterphone.app

import android.content.Context
import com.wireguard.config.Config
import com.wireguard.config.Interface
import com.wireguard.config.Peer
import org.json.JSONArray
import org.json.JSONObject

/**
 * בונה הגדרת WireGuard מהתשובה של השרת ומהמפתח הפרטי שנשמר במכשיר.
 *
 * FilterTube מוחרגת ברמת האפליקציה של Android (לא ברמת DNS):
 * בקשות הניגון יוצאות דרך חיבור המכשיר ולא דרך כתובת ה-IP של שרת FilterPhone.
 * אין דרך אמינה להחריג רק דומיינים של YouTube ב-WireGuard לפי שם; כתובות ה-IP
 * של YouTube דינמיות ומשותפות לשירותי Google. לכן החרגת החבילה היא המסלול היציב.
 */
object VpnConfigFactory {
    // החבילה הנוכחית וה-package הישן שהיה בשימוש בבניית בדיקה נפרדת.
    private val FILTERTUBE_PACKAGES = listOf(
        "com.filtertube.app",
        "com.filtertube.filtertube",
    )

    /**
     * מחזיר רק גרסאות FilterTube שמותקנות בפועל.
     * אסור להעביר חבילה חסרה ל-WireGuard — Android ייכשל בהקמת כל ה-VPN.
     */
    fun installedFilterTubePackages(context: Context): Set<String> =
        FILTERTUBE_PACKAGES.filterTo(linkedSetOf()) { packageName ->
            runCatching { context.packageManager.getApplicationInfo(packageName, 0) }.isSuccess
        }

    /**
     * useFallback: להתחבר לכתובת ה-IP של השרת (endpoint_fallback) במקום לשם —
     * כשה-DNS עוד לא זמין (מיד אחרי אתחול) או במצב "חסימה בלי VPN" כשהמנהרה למטה.
     */
    fun build(
        vpn: JSONObject,
        privateKey: String,
        context: Context,
        useFallback: Boolean = false,
    ): Config {
        val ifaceBuilder = Interface.Builder()
            .parsePrivateKey(privateKey)
            .parseAddresses(vpn.getString("address"))
            .parseDnsServers(vpn.getJSONArray("dns").joined())
            .setMtu(vpn.optInt("mtu", 1280))

        // ExcludedApplications מתורגם על ידי GoBackend ל-VpnService.Builder.addDisallowedApplication.
        // רק FilterTube (אם מותקנת) יוצאת ישירות; שאר האפליקציות נשארות בתוך ה-VPN.
        ifaceBuilder.excludeApplications(installedFilterTubePackages(context))
        val iface = ifaceBuilder.build()

        val fallback = vpn.optString("endpoint_fallback", "")
        val endpoint = if (useFallback && fallback.isNotEmpty()) fallback else vpn.getString("endpoint")
        val peer = Peer.Builder()
            .parsePublicKey(vpn.getString("server_public_key"))
            .parseEndpoint(endpoint)
            // 0.0.0.0/0 ו-::/0 — כל התעבורה של האפליקציות המוגנות במנהרה.
            .parseAllowedIPs(vpn.getJSONArray("allowed_ips").joined())
            .setPersistentKeepalive(vpn.optInt("persistent_keepalive", 25))
            .apply {
                val psk = vpn.optString("preshared_key", "")
                if (psk.isNotEmpty()) parsePreSharedKey(psk)
            }
            .build()

        return Config.Builder().setInterface(iface).addPeer(peer).build()
    }

    private fun JSONArray.joined(): String = (0 until length()).joinToString(",") { getString(it) }
}
