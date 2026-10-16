package com.filterphone.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdateBugNoticeTest {
    private val hour = 3600_000L
    private val updated = 1_800_000_000_000L

    @Test
    fun onlyAndroid16RightAfterAnUpdateAndOnce() {
        assertTrue(UpdateBugNotice.shouldShow(36, updated, notifiedFor = 0, now = updated + hour))
        assertFalse(UpdateBugNotice.shouldShow(35, updated, 0, updated + hour))      // אנדרואיד 15 — אין באג
        assertFalse(UpdateBugNotice.shouldShow(37, updated, 0, updated + hour))      // אנדרואיד 17 — תוקן
        assertFalse(UpdateBugNotice.shouldShow(36, updated, 0, updated + 7 * hour))  // מזמן — לא קשור לעדכון
        assertFalse(UpdateBugNotice.shouldShow(36, updated, updated, updated + hour)) // כבר התרענו על העדכון הזה
        assertFalse(UpdateBugNotice.shouldShow(36, 0, 0, updated))                    // לא ידוע מתי עודכן
    }
}
