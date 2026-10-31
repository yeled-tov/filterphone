package com.filterphone.crm

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** הבקשה היחידה של השירות: מה חדש מאז הסמן. */
object Api {
    class Unauthorized : IOException("401")

    fun poll(token: String, cursor: String?): Poll {
        val url = URL("${Crm.BASE}/v1/admin/notifications?after=" + URLEncoder.encode(cursor ?: "", "UTF-8"))
        val conn = url.openConnection() as HttpURLConnection
        try {
            conn.connectTimeout = 15_000
            conn.readTimeout = 20_000
            conn.setRequestProperty("Authorization", "Bearer $token")
            conn.setRequestProperty("Accept", "application/json")
            conn.setRequestProperty("User-Agent", "CrmFilterPhone/1")
            val code = conn.responseCode
            if (code == 401 || code == 403) throw Unauthorized()
            if (code !in 200..299) throw IOException("HTTP $code")
            val body = conn.inputStream.bufferedReader(Charsets.UTF_8).use { it.readText() }
            return Events.parse(body)
        } finally {
            conn.disconnect()
        }
    }
}
