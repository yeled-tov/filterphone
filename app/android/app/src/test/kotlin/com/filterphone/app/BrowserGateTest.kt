package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** הסייר המוגן עובד רק כש-FilterPhone באמת מגן על הטלפון. */
class BrowserGateTest {
    private fun decide(mode: String = SafeSites.FULL, configured: Boolean = true, paused: Boolean = false,
                       tunnelUp: Boolean = true, kosherNet: Boolean = false) =
        BrowserGate.decide(mode, configured, paused, tunnelUp, kosherNet)

    @Test
    fun opensOnlyWithActiveFiltering() {
        assertNull(decide())
        assertNull(decide(mode = SafeSites.ESSENTIAL))
        assertNull(decide(tunnelUp = false, kosherNet = true))          // רשת כשרה מסננת במקום המנהרה
        assertEquals(BrowserGate.Block.FILTER_OFF, decide(tunnelUp = false))
        assertEquals(BrowserGate.Block.PAUSED, decide(paused = true))
        assertEquals(BrowserGate.Block.NOT_SET_UP, decide(configured = false))
        assertEquals(BrowserGate.Block.OFF, decide(mode = SafeSites.OFF))
        // הסדר: כבוי בשרת קודם; טלפון שלא הופעל — לפני השהיה / חיבור
        assertEquals(BrowserGate.Block.OFF, decide(mode = SafeSites.OFF, configured = false, tunnelUp = false))
        assertEquals(BrowserGate.Block.NOT_SET_UP, decide(configured = false, paused = true, tunnelUp = false))
    }
}
