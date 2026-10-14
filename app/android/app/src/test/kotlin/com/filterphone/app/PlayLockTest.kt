package com.filterphone.app

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class PlayLockTest {
    private val waze = listOf("Waze: ניווט ועדכוני תנועה", "Waze Navigation & Live Traffic", "וייז")
    private val titles = waze.map { it.lowercase() }

    @After
    fun reset() = PlayLock.end()

    private fun verdict(texts: List<String>, search: Boolean = false) = PlayLock.verdict(texts, search, titles)

    @Test
    fun allowsTheTargetAppPageInHebrewAndEnglish() {
        val he = listOf("ניווט למעלה", "חיפוש ב-Google Play", "Waze: ניווט ועדכוני תנועה", "Waze", "התקנה",
            "עוד אפליקציות כאלה", "מוביט: ניווט ותכנון מסלולים")
        assertEquals(PlayLock.Verdict.ALLOW, verdict(he))
        val en = listOf("Navigate up", "Waze Navigation & Live Traffic", "Waze", "Install")
        assertEquals(PlayLock.Verdict.ALLOW, verdict(en))
        // בזמן ההתקנה ואחריה — עדיין העמוד של וייז
        assertEquals(PlayLock.Verdict.ALLOW, verdict(listOf("Waze: ניווט ועדכוני תנועה", "ביטול", "45%")))
        assertEquals(PlayLock.Verdict.ALLOW, verdict(listOf("Waze: ניווט ועדכוני תנועה", "הסרת התקנה", "פתיחה")))
    }

    @Test
    fun returnsFromAnotherAppsPage() {
        // לחצו על "אפליקציות דומות" — עמוד של מוביט, עם כפתור התקנה, בלי וייז בראש המסך
        val moovit = listOf("ניווט למעלה", "מוביט: ניווט ותכנון מסלולים", "Moovit", "התקנה")
        assertEquals(PlayLock.Verdict.RETURN, verdict(moovit))
        assertEquals(PlayLock.Verdict.RETURN, verdict(listOf("Some Game", "Install")))
    }

    @Test
    fun returnsFromSearchAndPlayHome() {
        assertEquals(PlayLock.Verdict.RETURN, verdict(listOf("חיפוש אפליקציות ומשחקים"), search = true))
        assertEquals(PlayLock.Verdict.RETURN, verdict(listOf("בשבילך", "המובילים", "משחקים", "אפליקציות", "ספרים")))
        assertEquals(PlayLock.Verdict.RETURN, verdict(listOf("For you", "Top charts", "Games", "Apps")))
    }

    @Test
    fun allowsInstallDialogsThatAreNotNavigation() {
        // בחירת חשבון / השלמת הגדרות / טעינה — אין כאן ניווט החוצה
        assertEquals(PlayLock.Verdict.ALLOW, verdict(listOf("בחירת חשבון", "המשך")))
        assertEquals(PlayLock.Verdict.ALLOW, verdict(emptyList()))
    }

    @Test
    fun titleFarDownThePageDoesNotUnlockAnotherApp() {
        // עמוד של אפליקציה אחרת שבו וייז מופיעה רק ב"אפליקציות דומות" (הרבה מתחת לראש המסך)
        val other = listOf("Other App") + List(PlayLock.HEAD_TEXTS) { "פסקה $it" } + listOf("Waze: ניווט ועדכוני תנועה", "התקנה")
        assertEquals(PlayLock.Verdict.RETURN, verdict(other))
    }

    @Test
    fun sessionClosesAMinuteAfterTheInstallFinishes() {
        PlayLock.start("com.waze", waze, versionAtStart = null, now = 0)
        assertNotNull(PlayLock.active(1_000))
        PlayLock.onVersion(null, 2_000)                       // עוד לא הותקנה
        assertNotNull(PlayLock.active(PlayLock.WINDOW_MS - 1))
        PlayLock.onVersion(1234, 10_000)                       // הותקנה
        assertNotNull(PlayLock.active(10_000 + PlayLock.AFTER_INSTALL_MS - 1))
        assertNull(PlayLock.active(10_000 + PlayLock.AFTER_INSTALL_MS))
    }

    @Test
    fun updateIsDetectedByVersionChange() {
        PlayLock.start("com.waze", waze, versionAtStart = 100, now = 0)
        PlayLock.onVersion(100, 5_000)                         // אותה גרסה — עדיין מעדכן
        assertNotNull(PlayLock.active(PlayLock.WINDOW_MS - 1))
        PlayLock.onVersion(101, 6_000)
        assertNull(PlayLock.active(6_000 + PlayLock.AFTER_INSTALL_MS))
    }

    @Test
    fun windowExpiresByItself() {
        PlayLock.start("com.waze", waze, versionAtStart = null, now = 0)
        assertNull(PlayLock.active(PlayLock.WINDOW_MS))
    }
}
