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

    @Test
    fun curtainStartsBelowTheMainButtonRow() {
        val h = 2400
        val page = listOf(
            PlayLock.Box("ניווט למעלה", 90, 200),
            PlayLock.Box("Waze: ניווט ועדכוני תנועה", 300, 380),
            PlayLock.Box("4.5 ★", 600, 650),
            PlayLock.Box("התקנה", 760, 860),
            PlayLock.Box("מוביט: ניווט ותכנון מסלולים", 1500, 1560),
            PlayLock.Box("התקנה", 1600, 1680),                 // כפתור של אפליקציה דומה — נמוך יותר
        )
        assertEquals(890, PlayLock.curtainTop(page, h, 30))
        // מותקנת: "הסרת התקנה" + "פתיחה" באותה שורה — מתחת לנמוך מביניהם
        val installed = listOf(PlayLock.Box("הסרת התקנה", 760, 858), PlayLock.Box("פתיחה", 755, 862))
        assertEquals(872, PlayLock.curtainTop(installed, h, 10))
        // בזמן ההורדה: "ביטול"
        assertEquals(870, PlayLock.curtainTop(listOf(PlayLock.Box("Cancel", 770, 860)), h, 10))
    }

    @Test
    fun noCurtainWithoutAButtonInASaneSpot() {
        val h = 2400
        assertNull(PlayLock.curtainTop(emptyList(), h, 10))
        assertNull(PlayLock.curtainTop(listOf(PlayLock.Box("Waze", 300, 380)), h, 10))
        // הכפתור נגלל אל מתחת לסרגל העליון / בתחתית המסך — לא מכסים לפיו
        assertNull(PlayLock.curtainTop(listOf(PlayLock.Box("התקנה", 40, 150)), h, 10))
        assertNull(PlayLock.curtainTop(listOf(PlayLock.Box("התקנה", 2100, 2200)), h, 10))
        assertNull(PlayLock.curtainTop(listOf(PlayLock.Box("התקנה", 800, 900)), 0, 10))
    }

    @Test
    fun targetPageIsRecognisedOnlyByTheHeader() {
        assertEquals(true, PlayLock.isTargetPage(listOf("ניווט למעלה", "Waze: ניווט ועדכוני תנועה", "התקנה"), titles))
        assertEquals(false, PlayLock.isTargetPage(listOf("מוביט", "התקנה"), titles))
        val far = List(PlayLock.HEAD_TEXTS) { "פסקה $it" } + listOf("Waze: ניווט ועדכוני תנועה")
        assertEquals(false, PlayLock.isTargetPage(far, titles))
    }
}
