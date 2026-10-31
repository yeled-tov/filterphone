package com.filterphone.crm

import org.json.JSONObject

/** אירוע מהשרת (GET /v1/admin/notifications — ראו server/filterphone/api/notify.py). */
data class Event(val kind: String, val key: String, val title: String, val text: String, val route: String, val ts: Long)

data class Poll(val cursor: String, val events: List<Event>, val pending: Int)

/** פענוח תשובת השרת ומזהי התראות. בלי תלות באנדרואיד — נבדק ביחידה. */
object Events {
    const val ID_SERVICE = 1
    const val ID_SUMMARY = 2
    const val ID_RELOGIN = 3
    const val ID_UPDATE = 4

    fun parse(body: String): Poll {
        val o = JSONObject(body)
        val arr = o.optJSONArray("events")
        val out = ArrayList<Event>()
        for (i in 0 until (arr?.length() ?: 0)) {
            val e = arr!!.optJSONObject(i) ?: continue
            val key = e.optString("key")
            val title = e.optString("title")
            if (key.isEmpty() || title.isEmpty()) continue
            out += Event(e.optString("kind"), key, title.take(120), e.optString("text").take(400),
                e.optString("route"), e.optLong("ts", 0))
        }
        val counts = o.optJSONObject("counts")
        val pending = if (counts == null) 0 else
            counts.optInt("requests") + counts.optInt("support") + counts.optInt("web")
        return Poll(o.optString("cursor"), out, pending)
    }

    /** מזהה קבוע לכל נושא (אותה שיחה — אותה התראה, מתעדכנת), שלא מתנגש במזהים הקבועים. טהור. */
    fun notificationId(key: String): Int = 100 + Math.floorMod(key.hashCode(), 1_000_000_000)
}
