package com.filterphone.app

import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/**
 * בדיקה שתעבורה באמת עוברת במנהרה — לא רק handshake טרי.
 *
 * handshake של WireGuard עובר גם כשה-DNS (AdGuard), הפרוקסי או היציאה לאינטרנט בשרת תקועים —
 * ואז "ה-VPN מחובר" אבל אין אינטרנט בטלפון, ואף אחד לא יודע. כאן: בקשת HTTP אמיתית (פורט 80 — עוברת
 * בפרוקסי ויוצאת לאינטרנט מהשרת), כמו שאפליקציה אחרת הייתה עושה.
 *
 * כדי לא להתריע לשווא: כל תשובת HTTP (גם דף חסימה) = התעבורה עוברת; אתר שהמדיניות חוסמת ב-DNS
 * (0.0.0.0) לא נחשב כשל; ויש שני יעדים — מספיק שאחד עונה.
 * socket ישיר: האפליקציה אוסרת cleartext ב-URLConnection (usesCleartextTraffic=false), וזו רק בדיקה.
 */
object DataProbe {
    /** האתר שלנו (לא נחסם במדיניות) ובדיקת החיבור של אנדרואיד. */
    val TARGETS = listOf("filterphone.com", "connectivitycheck.gstatic.com")
    private const val TIMEOUT_MS = 5_000

    /** null = תעבורה עוברת. אחרת שלב הכשל של היעד הראשון: dns / blocked / connect / timeout / http:bad. */
    fun run(targets: List<String> = TARGETS): String? {
        var firstFailure: String? = null
        for (host in targets) {
            val failure = probe(host) ?: return null
            if (firstFailure == null) firstFailure = failure
        }
        return firstFailure
    }

    private fun probe(host: String): String? {
        val addr = try {
            // IPv4 קודם: למנהרה אין כתובת IPv6, וכתובת כזו הייתה נכשלת "בכוונה" ונספרת כתקלה
            InetAddress.getAllByName(host).let { all -> all.firstOrNull { it is Inet4Address } ?: all.firstOrNull() }
                ?: return "dns"
        } catch (_: UnknownHostException) {
            return "dns"
        } catch (_: Exception) {
            return "dns"
        }
        if (addr.isAnyLocalAddress || addr.isLoopbackAddress) return "blocked"   // נחסם במדיניות — לא תקלה
        return try {
            Socket().use { s ->
                s.connect(InetSocketAddress(addr, 80), TIMEOUT_MS)
                s.soTimeout = TIMEOUT_MS
                s.getOutputStream().apply {
                    write(request(host).toByteArray(Charsets.US_ASCII))
                    flush()
                }
                parseStatus(BufferedReader(InputStreamReader(s.getInputStream(), Charsets.US_ASCII)).readLine())
            }
        } catch (_: SocketTimeoutException) {
            "timeout"
        } catch (_: Exception) {
            "connect"
        }
    }

    fun request(host: String): String =
        "GET /generate_204 HTTP/1.1\r\nHost: $host\r\nUser-Agent: FilterPhone-probe\r\nConnection: close\r\n\r\n"

    /** טהור — נבדק ביחידה. כל שורת סטטוס HTTP תקינה (גם 30x / 403 של דף חסימה) = התעבורה עוברת. */
    fun parseStatus(statusLine: String?): String? {
        val parts = statusLine?.trim()?.split(' ') ?: return "http:bad"
        if (parts.size < 2 || !parts[0].startsWith("HTTP/")) return "http:bad"
        val code = parts[1].toIntOrNull() ?: return "http:bad"
        return if (code in 100..599) null else "http:bad"
    }
}
