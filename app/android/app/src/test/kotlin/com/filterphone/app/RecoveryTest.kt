package com.filterphone.app

import java.util.Calendar
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RecoveryTest {
    private val day = 24 * 60 * 60_000L

    private fun hourOf(ms: Long): Triple<Int, Int, Int> {
        val c = Calendar.getInstance().apply { timeInMillis = ms }
        return Triple(c.get(Calendar.HOUR_OF_DAY), c.get(Calendar.MINUTE), c.get(Calendar.SECOND))
    }

    @Test
    fun dailyDelayLandsOnTheTargetHourAndIsWithinADay() {
        val now = System.currentTimeMillis()
        for (hour in listOf(0, 2, 9, 23)) {
            val delay = Recovery.delayToHourMs(now, hour)
            assertTrue("חיובי", delay > 0)
            assertTrue("עד 24 שעות", delay <= day)
            assertEquals(Triple(hour, 0, 0), hourOf(now + delay))   // נוחת בדיוק על השעה
        }
    }

    @Test
    fun whenThePastHourTodayItTargetsTomorrow() {
        // 03:00 מקומי, יעד 02:00 → מחר (בין 23 ל-24 שעות)
        val threeAm = Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, 3); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
        }.timeInMillis
        val delay = Recovery.delayToHourMs(threeAm, 2)
        assertEquals(23 * 60 * 60_000L, delay)
    }

    @Test
    fun jitterOnlyAddsAndNeverGoesNegative() {
        val now = System.currentTimeMillis()
        val base = Recovery.delayToHourMs(now, 2, 0)
        assertEquals(base + 5 * 60_000L, Recovery.delayToHourMs(now, 2, 5 * 60_000L))
        assertEquals(base, Recovery.delayToHourMs(now, 2, -1))      // ג'יטר שלילי מתעלמים ממנו
    }
}
