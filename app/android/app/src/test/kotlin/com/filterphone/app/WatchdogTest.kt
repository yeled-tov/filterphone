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

    @Test
    fun xiaomiStoppedGuardAndBlockedBypassAreReported() {
        // המגן מופעל אבל נעצר (שיאומי) — רק אחרי כמה דקות, לא רגע אחרי הפעלה
        assertEquals(listOf(Watchdog.GUARD_STALE),
            Watchdog.missing(true, guardNeeded = true, guardOn = true, batteryOk = true, guardStale = true))
        assertEquals(false, Watchdog.staleLongEnough(since = 1_000L, now = 1_000L + 60_000L))
        assertEquals(true, Watchdog.staleLongEnough(since = 1_000L, now = 1_000L + 4 * 60_000L))
        assertEquals(false, Watchdog.staleLongEnough(since = 0L, now = 10_000_000L))
        // שיאומי: "הפעלה אוטומטית" כבויה
        assertEquals(listOf(Watchdog.AUTOSTART),
            Watchdog.missing(true, guardNeeded = true, guardOn = true, batteryOk = true, autostartOk = false))
        // lockdown בלי מנהל מכשיר — FilterTube בלי רשת
        assertEquals(listOf(Watchdog.BYPASS_BLOCKED),
            Watchdog.missing(true, guardNeeded = false, guardOn = false, batteryOk = true, bypassBlocked = true))
        assertEquals(true, Xiaomi.isXiaomi("Xiaomi", "Redmi"))
        assertEquals(true, Xiaomi.isXiaomi("Xiaomi", "POCO"))
        assertEquals(false, Xiaomi.isXiaomi("samsung", "samsung"))
    }
}
