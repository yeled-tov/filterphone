package com.filterphone.app

import com.wireguard.config.Config
import com.wireguard.config.Interface
import com.wireguard.config.Peer
import org.json.JSONArray
import org.json.JSONObject

/** בונה הגדרת WireGuard מהתשובה של השרת (vpn) ומהמפתח הפרטי שנשמר במכשיר. */
object VpnConfigFactory {

    /** Both FilterTube package IDs must bypass the VPN tunnel. */
    internal val FILTERTUBE_PACKAGES = setOf(
        "com.filtertube.app",
        "com.filtertube.filtertube",
    )


    /**
     * useFallback: להתחבר לכתובת ה-IP של השרת (endpoint_fallback) במקום לשם —
     * כשה-DNS עוד לא זמין (מיד אחרי אתחול) או במצב "חסימה בלי VPN" כשהמנהרה למטה.
     */
    fun build(vpn: JSONObject, privateKey: String, useFallback: Boolean = false): Config {
        val ifaceBuilder = Interface.Builder()
            .parsePrivateKey(privateKey)
            .parseAddresses(vpn.getString("address"))
            .parseDnsServers(vpn.getJSONArray("dns").joined())
            .setMtu(vpn.optInt("mtu", 1280))

        // FilterTube bypasses the WireGuard tunnel; its traffic uses the normal network.
        FILTERTUBE_PACKAGES.forEach { ifaceBuilder.addExcludedApplication(it) }
        val iface = ifaceBuilder.build()

        val fallback = vpn.optString("endpoint_fallback", "")
        val endpoint = if (useFallback && fallback.isNotEmpty()) fallback else vpn.getString("endpoint")
        val peer = Peer.Builder()
            .parsePublicKey(vpn.getString("server_public_key"))
            .parseEndpoint(endpoint)
            // 0.0.0.0/0 ו-::/0 — כל התעבורה במנהרה; IPv6 בלי כתובת נחסם (לא דולף החוצה)
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
