package com.filterphone.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PlayGateTest {
    @Test
    fun gateOnlyWithManagedPlayOnADeviceOwnerThatIsNotPaused() {
        assertTrue(PlayGate.applies(playManaged = true, deviceOwner = true, paused = false))
        assertFalse(PlayGate.applies(playManaged = false, deviceOwner = true, paused = false))
        assertFalse(PlayGate.applies(playManaged = true, deviceOwner = false, paused = false))   // בלי מנהל מכשיר — אי אפשר
        assertFalse(PlayGate.applies(playManaged = true, deviceOwner = true, paused = true))     // השהיה = פתוח לגמרי
    }

    @Test
    fun opensForAFewMinutesAtMost() {
        assertFalse(PlayGate.isOpen(0L, 1_000L))
        assertTrue(PlayGate.isOpen(1_000L, 1_000L + 60_000L))
        assertFalse(PlayGate.isOpen(1_000L, 1_000L + PlayGate.MAX_OPEN_MS))
        assertFalse(PlayGate.isOpen(5_000L, 1_000L))           // שעון שזז אחורה — סגור
    }

    @Test
    fun nightUpdatesOnlyWhileChargingWithTheScreenOff() {
        assertTrue(PlayGate.nightWindow(hour = 3, charging = true, interactive = false))
        assertFalse(PlayGate.nightWindow(hour = 3, charging = false, interactive = false))
        assertFalse(PlayGate.nightWindow(hour = 3, charging = true, interactive = true))
        assertFalse(PlayGate.nightWindow(hour = 14, charging = true, interactive = false))
        assertFalse(PlayGate.nightWindow(hour = 5, charging = true, interactive = false))
    }
}
