package com.filterphone.app

import com.wireguard.config.Config
import com.wireguard.config.Interface
import com.wireguard.config.Peer
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.StringReader

/**
 * בונה הגדרת WireGuard מהתשובה של השרת ומהמפתח הפרטי שנשמר במכשיר.
 *
 * excluded — אפליקציות שעוברות מחוץ למנהרה (VpnBypass: FilterTube וכו'). GoBackend מתרגם את זה
 * ל-VpnService.Builder.addDisallowedApplication. חייבות להיות מותקנות — חבילה חסרה מפילה את כל ה-VPN,
 * ולכן הרשימה נבנית רק ממה שמותקן (VpnBypass.current).
 */
object VpnConfigFactory {

    /**
     * useFallback: להתחבר לכתובת ה-IP של השרת (endpoint_fallback) במקום לשם —
     * כשה-DNS עוד לא זמין (מיד אחרי אתחול) או במצב "חסימה בלי VPN" כשהמנהרה למטה.
     */
    fun build(
        vpn: JSONObject,
        privateKey: String,
        excluded: Set<String> = emptySet(),
        useFallback: Boolean = false,
    ): Config {
        val iface = Interface.Builder()
            .parsePrivateKey(privateKey)
            .parseAddresses(vpn.getString("address"))
            .parseDnsServers(vpn.getJSONArray("dns").joined())
            .setMtu(vpn.optInt("mtu", 1280))
            .excludeApplications(excluded)
            .build()

        val fallback = vpn.optString("endpoint_fallback", "")
        val endpoint = if (useFallback && fallback.isNotEmpty()) fallback else vpn.getString("endpoint")
        val peer = Peer.Builder()
            .parsePublicKey(vpn.getString("server_public_key"))
            .parseEndpoint(endpoint)
            // 0.0.0.0/0 ו-::/0 — כל התעבורה (חוץ מהאפליקציות המוחרגות) במנהרה; IPv6 בלי כתובת נחסם
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
     * הגדרה בפורמט הטקסט הישן (מכשירים שנרשמו לפני endpoint_fallback): אותה החרגה, בלי לשנות שרת,
     * כתובות או מפתחות. טקסט בלי [Interface] — נשאר כמו שהוא (לא מסכנים את החיבור).
     */
    fun buildLegacy(rawConfig: String, excluded: Set<String> = emptySet()): Config =
        Config.parse(BufferedReader(StringReader(withExcluded(rawConfig, excluded))))

    /** מוסיף/ממזג שורת ExcludedApplications לסעיף [Interface]. טהור — נבדק ביחידה. */
    fun withExcluded(rawConfig: String, excluded: Set<String>): String {
        if (excluded.isEmpty()) return rawConfig
        val lines = rawConfig.lines().toMutableList()
        val existing = lines.indexOfFirst {
            it.substringBefore("#").trim().startsWith("ExcludedApplications", ignoreCase = true)
        }
        if (existing >= 0) {
            val current = lines[existing].substringAfter("=", "").substringBefore("#")
                .split(",").map { it.trim() }.filter { it.isNotEmpty() }
            lines[existing] = "ExcludedApplications = " + (current + excluded).distinct().joinToString(", ")
        } else {
            val header = lines.indexOfFirst { it.trim().equals("[Interface]", ignoreCase = true) }
            if (header < 0) return rawConfig
            lines.add(header + 1, "ExcludedApplications = " + excluded.joinToString(", "))
        }
        return lines.joinToString("\n")
    }

    private fun JSONArray.joined(): String = (0 until length()).joinToString(",") { getString(it) }
}
