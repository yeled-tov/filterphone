package com.filterphone.crm

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class EventsTest {
    @Test
    fun parsesServerEventsAndCounts() {
        val body = """{"cursor":"r3.s9.w1","counts":{"requests":2,"support":1,"web":0},"events":[
            {"kind":"join","key":"r3","title":"בקשת הצטרפות חדשה","text":"משה · גלקסי","route":"#/requests","ts":1700000000},
            {"kind":"support","key":"s7","title":"הודעה ממשה","text":"אין אינטרנט","route":"#/support/7","ts":1700000100},
            {"kind":"web","key":"","title":"בלי מפתח — מדלגים"}]}"""
        val p = Events.parse(body)
        assertEquals("r3.s9.w1", p.cursor)
        assertEquals(3, p.pending)
        assertEquals(listOf("r3", "s7"), p.events.map { it.key })
        assertEquals("#/support/7", p.events[1].route)
        assertEquals(1700000100L, p.events[1].ts)
    }

    @Test
    fun emptyAnswerIsSafe() {
        val p = Events.parse("{}")
        assertEquals("", p.cursor)
        assertTrue(p.events.isEmpty())
        assertEquals(0, p.pending)
    }

    @Test
    fun notificationIdsAreStableAndNeverReserved() {
        assertEquals(Events.notificationId("s7"), Events.notificationId("s7"))
        assertNotEquals(Events.notificationId("s7"), Events.notificationId("s8"))
        for (k in listOf("r1", "s1", "w1", "", "x".repeat(500))) {
            val id = Events.notificationId(k)
            assertTrue(id >= 100)
            assertFalse(id == Events.ID_SERVICE || id == Events.ID_SUMMARY || id == Events.ID_RELOGIN)
        }
    }

    @Test
    fun onlyKnownDashboardRoutesAreOpened() {
        assertEquals("#/support/7", Crm.safeRoute("#/support/7"))
        assertEquals("#/requests", Crm.safeRoute("#/requests"))
        assertNull(Crm.safeRoute("javascript:alert(1)"))
        assertNull(Crm.safeRoute("#/support/7';alert(1)//"))
        assertNull(Crm.safeRoute("https://evil.example/#/x"))
        assertNull(Crm.safeRoute(null))
        assertEquals("https://api.filterphone.com/admin/?app=1#/web", Crm.dashboardUrl("#/web"))
        assertEquals("https://api.filterphone.com/admin/?app=1", Crm.dashboardUrl("bad"))
    }

    @Test
    fun tokensFromTheDashboardAreValidated() {
        assertTrue(Crm.validToken("Ab3_-xYz".repeat(5)))
        assertFalse(Crm.validToken(null))
        assertFalse(Crm.validToken("short"))
        assertFalse(Crm.validToken("a".repeat(20) + "\"<script>"))
        assertFalse(Crm.validToken("א".repeat(30)))
    }
}

class UpdatesTest {
    @Test
    fun readsTheVersionFromCrmJson() {
        assertEquals(97L, Updates.parseVersion("""{"name":"CRM-FilterPhone.apk","version_code":97}"""))
        assertEquals(-1L, Updates.parseVersion("not json"))
        assertEquals(-1L, Updates.parseVersion("{}"))
    }

    @Test
    fun notifiesOnceAndOnlyForNewerVersions() {
        assertTrue(Updates.shouldNotify(latest = 98, current = 97, alreadyNotified = -1))
        assertFalse(Updates.shouldNotify(latest = 98, current = 97, alreadyNotified = 98))
        assertFalse(Updates.shouldNotify(latest = 97, current = 97, alreadyNotified = -1))
        assertFalse(Updates.shouldNotify(latest = -1, current = 97, alreadyNotified = -1))
    }
}
