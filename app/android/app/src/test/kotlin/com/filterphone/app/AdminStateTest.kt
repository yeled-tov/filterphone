package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AdminStateTest {
    @Test
    fun remainingTimeUsesServerClockNotDevice() {
        // 30 דקות לפי זמן השרת → 1,800,000 מילישניות, גם אם שעון המכשיר שונה
        assertEquals(1_800_000L, AdminState.remainMs(untilServerS = 2_000L, serverNowS = 200L))
    }

    @Test
    fun expiredOrPastPauseIsZeroNotNegative() {
        assertEquals(0L, AdminState.remainMs(untilServerS = 100L, serverNowS = 500L))
        assertEquals(0L, AdminState.remainMs(untilServerS = 0L, serverNowS = 0L))
    }

    @Test
    fun dismissedPauseIsIgnoredButNewPauseNonceIsAccepted() {
        assertTrue(AdminState.shouldIgnorePause("pause-a", "pause-a"))
        assertFalse(AdminState.shouldIgnorePause("pause-b", "pause-a"))
        assertFalse(AdminState.shouldIgnorePause(null, "pause-a"))
        assertFalse(AdminState.shouldIgnorePause("", ""))
    }
}
