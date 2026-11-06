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
    fun updatesStayOpenLongerThanASingleInstall() {
        // עדכונים ("עדכון הכול") — עד 10 דקות מאז העדכון האחרון שרץ; התקנה — 5 דקות לכל היותר
        assertTrue(PlayGate.isOpen(1_000L, 1_000L + 7 * 60_000L, PlayGate.UPDATE_MAX_MS))
        assertFalse(PlayGate.isOpen(1_000L, 1_000L + 7 * 60_000L))
        assertFalse(PlayGate.isOpen(1_000L, 1_000L + PlayGate.UPDATE_MAX_MS, PlayGate.UPDATE_MAX_MS))
    }

    @Test
    fun nightUpdatesOnlyWhileChargingWithTheScreenOff() {
        assertTrue(PlayGate.nightWindow(hour = 3, charging = true, interactive = false))
        assertFalse(PlayGate.nightWindow(hour = 3, charging = false, interactive = false))
        assertFalse(PlayGate.nightWindow(hour = 3, charging = true, interactive = true))
        assertFalse(PlayGate.nightWindow(hour = 14, charging = true, interactive = false))
        assertFalse(PlayGate.nightWindow(hour = 5, charging = true, interactive = false))
    }

    @Test
    fun managedPlayHoldsInstallsOnlyInPlayAndShortlyAfter() {
        val now = 10_000_000L
        assertTrue(PlayGate.playActive(inPlay = true, playAt = 0L, now = now))                  // בתוך Play
        assertTrue(PlayGate.playActive(inPlay = false, playAt = now - 60_000L, now = now))      // יצאו לפני דקה
        assertFalse(PlayGate.playActive(inPlay = false, playAt = now - PlayGate.PLAY_HOLD_MS - 1, now = now))
        assertFalse(PlayGate.playActive(inPlay = false, playAt = 0L, now = now))                // מחוץ ל-Play — APK פתוח
    }
}
