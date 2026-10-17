package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WatchdogTest {
    @Test
    fun reportsOnlyWhatIsReallyMissing() {
        assertEquals(emptyList<String>(), Watchdog.missing(vpnPermission = true, guardNeeded = true, guardOn = true, batteryOk = true))
        assertEquals(listOf(Watchdog.VPN), Watchdog.missing(false, guardNeeded = false, guardOn = false, batteryOk = true))
        // המגן כבוי — אבל המנהל לא חסם שום דבר שדורש אותו: לא חסר
        assertEquals(emptyList<String>(), Watchdog.missing(true, guardNeeded = false, guardOn = false, batteryOk = null))
        assertEquals(listOf(Watchdog.VPN, Watchdog.GUARD, Watchdog.BATTERY),
            Watchdog.missing(false, guardNeeded = true, guardOn = false, batteryOk = false))
    }

    @Test
    fun alertsOnChangeAndAgainOnlyAfterSixHours() {
        val now = 1_800_000_000_000L
        assertTrue(Watchdog.shouldAlert(listOf("guard"), alerted = "", alertedAt = 0, now = now))
        assertFalse(Watchdog.shouldAlert(listOf("guard"), alerted = "guard", alertedAt = now - 60_000, now = now))
        assertTrue(Watchdog.shouldAlert(listOf("guard", "vpn"), alerted = "guard", alertedAt = now - 60_000, now = now))
        assertTrue(Watchdog.shouldAlert(listOf("guard"), alerted = "guard", alertedAt = now - 7 * 3600_000L, now = now))
        assertFalse(Watchdog.shouldAlert(emptyList(), alerted = "", alertedAt = 0, now = now))
    }
}
