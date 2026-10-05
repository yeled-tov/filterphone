package com.filterphone.app

import com.wireguard.config.Config
import com.wireguard.config.Interface
import com.wireguard.config.Peer
import org.json.JSONArray
import org.json.JSONObject

/** בונה הגדרת WireGuard מהתשובה של השרת (vpn) ומהמפתח הפרטי שנשמר במכשיר. */
object VpnConfigFactory {

    fun build(vpn: JSONObject, privateKey: String): Config {
        val iface = Interface.Builder()
            .parsePrivateKey(privateKey)
            .parseAddresses(vpn.getString("address"))
            .parseDnsServers(vpn.getJSONArray("dns").joined())
            .setMtu(vpn.optInt("mtu", 1280))
            .build()

        val peer = Peer.Builder()
            .parsePublicKey(vpn.getString("server_public_key"))
            .parseEndpoint(vpn.getString("endpoint"))
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
