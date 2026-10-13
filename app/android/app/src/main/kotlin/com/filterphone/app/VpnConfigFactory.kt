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
 * האפליקציה הרשמית של YouTube מוחרגת ברמת Android (לא ברמת DNS):
 * תעבורת YouTube יוצאת דרך חיבור המכשיר ולא דרך כתובת ה-IP של שרת FilterPhone.
 * אין דרך אמינה להחריג רק דומיינים של YouTube ב-WireGuard לפי שם; כתובות ה-IP
 * של YouTube דינמיות ומשותפות לשירותי Google. לכן החרגת החבילה הרשמית היא המסלול היציב.
 */
object VpnConfigFactory {
    // החבילה הנוכחית וה-package הישן שהיה בשימוש בבניית בדיקה נפרדת.
    private const val YOUTUBE_PACKAGE = "com.google.android.youtube"

    /**
     * מחזיר את YouTube הרשמית רק אם היא מותקנת בפועל.
     * אסור להעביר חבילה חסרה ל-WireGuard — Android ייכשל בהקמת כל ה-VPN.
     */
    fun installedYouTubePackages(context: Context): Set<String> =
        if (runCatching { context.packageManager.getApplicationInfo(YOUTUBE_PACKAGE, 0) }.isSuccess) {
            setOf(YOUTUBE_PACKAGE)
        } else {
            emptySet()
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
        // רק YouTube הרשמית (אם מותקנת) יוצאת ישירות; FilterTube וכל שאר האפליקציות נשארות בתוך ה-VPN.
        ifaceBuilder.excludeApplications(installedYouTubePackages(context))
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

    /**
     * תאימות למכשירים ישנים ששמרו הגדרת WireGuard בפורמט טקסט.
     * מוסיף את אותה החרגת YouTube גם להגדרות הישנות, בלי לשנות שרתים, כתובות או מפתחות.
     */
    fun buildLegacy(rawConfig: String, context: Context): Config {
        val packages = installedYouTubePackages(context)
        if (packages.isEmpty()) {
            return Config.parse(java.io.BufferedReader(java.io.StringReader(rawConfig)))
        }

        val lines = rawConfig.lines().toMutableList()
        val excludedLine = lines.indexOfFirst {
            it.substringBefore("#").trim().startsWith("ExcludedApplications", ignoreCase = true)
        }
        if (excludedLine >= 0) {
            val existing = lines[excludedLine]
                .substringAfter("=", "")
                .substringBefore("#")
                .split(",")
                .map { it.trim() }
                .filter { it.isNotEmpty() }
            lines[excludedLine] = "ExcludedApplications = " + (existing + packages).distinct().joinToString(", ")
        } else {
            val interfaceHeader = lines.indexOfFirst { it.trim().equals("[Interface]", ignoreCase = true) }
            // הגדרת K_CONFIG הישנה אמורה להיות WireGuard config תקני עם [Interface].
            // אם היא לא כזו, נשאיר אותה כפי שהיא במקום לסכן את חיבור ה-VPN.
            if (interfaceHeader < 0) {
                return Config.parse(java.io.BufferedReader(java.io.StringReader(rawConfig)))
            }
            lines.add(interfaceHeader + 1, "ExcludedApplications = " + packages.joinToString(", "))
        }
        return Config.parse(java.io.BufferedReader(java.io.StringReader(lines.joinToString("\n"))))
    }

    private fun JSONArray.joined(): String = (0 until length()).joinToString(",") { getString(it) }
}
