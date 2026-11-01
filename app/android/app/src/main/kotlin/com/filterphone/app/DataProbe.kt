package com.filterphone.app

import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import kotlin.random.Random

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

    /**
     * התעבורה של הטלפון באמת עוברת במנהרה? שאילתת DNS לשרת ה-DNS של המנהרה (10.8.0.1 — כתובת פנימית
     * שמגיעים אליה רק דרך ה-VPN). תשובה = במנהרה. אין תשובה בזמן שבדיקת ה-HTTP (run) עברה = התעבורה
     * עוקפת את ה-VPN: המנהרה "מחוברת" ו-handshake טרי, אבל אנדרואיד לא מנתב דרכה (קרה אחרי עדכון,
     * כשה-VPN לא נועל — ואז האינטרנט עובד בלי סינון ואף בדיקה אחרת לא מרגישה).
     * null = אי אפשר לבדוק (אין כתובת DNS פנימית בהגדרה).
     */
    fun viaTunnel(dnsIp: String?): Boolean? {
        val ip = dnsIp?.trim()?.takeIf { IPV4.matches(it) } ?: return null
        val addr = runCatching { InetAddress.getByName(ip) }.getOrNull() ?: return null   // כתובת מספרית — בלי DNS
        if (!addr.isSiteLocalAddress) return null          // שרת ציבורי — מגיעים אליו גם בלי המנהרה
        repeat(2) {
            try {
                DatagramSocket().use { s ->
                    s.soTimeout = 2_500
                    val id = Random.nextInt(1, 0xFFFF)
                    val q = dnsQuery(id, "filterphone.com")
                    s.send(DatagramPacket(q, q.size, addr, 53))
                    val buf = ByteArray(512)
                    val p = DatagramPacket(buf, buf.size)
                    s.receive(p)
                    if (answerId(buf, p.length) == id) return true
                }
            } catch (_: SocketTimeoutException) {
                // ננסה שוב פעם אחת
            } catch (_: Exception) {
                return false                                // אין מסלול לכתובת הפנימית — לא דרך המנהרה
            }
        }
        return false
    }

    private val IPV4 = Regex("^\\d{1,3}(\\.\\d{1,3}){3}$")

    /** שאילתת DNS מסוג A (טהור — נבדק ביחידה). */
    fun dnsQuery(id: Int, host: String): ByteArray {
        val name = host.trimEnd('.').split('.').flatMap { label ->
            listOf(label.length.toByte()) + label.toByteArray(Charsets.US_ASCII).toList()
        }
        val header = byteArrayOf((id shr 8).toByte(), id.toByte(), 0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0)
        return header + name.toByteArray() + byteArrayOf(0, 0, 1, 0, 1)
    }

    /** מזהה התשובה (שני הבתים הראשונים), -1 אם קצרה מדי. טהור. */
    fun answerId(buf: ByteArray, length: Int): Int =
        if (length < 12) -1 else ((buf[0].toInt() and 0xff) shl 8) or (buf[1].toInt() and 0xff)

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
