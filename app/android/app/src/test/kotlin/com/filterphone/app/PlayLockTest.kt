package com.filterphone.app

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
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
    fun readsTheStateOfTheAppPageFromItsButtons() {
        val head = listOf("Waze: ניווט ועדכוני תנועה", "4.5 ★", "מעל 100 מיליון התקנות")
        assertEquals(PlayLock.Action.INSTALL, PlayLock.action(head + "התקנה"))
        assertEquals(PlayLock.Action.UPDATE, PlayLock.action(head + listOf("הסרת התקנה", "עדכון")))
        assertEquals(PlayLock.Action.INSTALLING, PlayLock.action(head + listOf("ממתין להורדה", "ביטול")))
        assertEquals(PlayLock.Action.INSTALLING, PlayLock.action(head + listOf("45% מתוך 80MB")))
        assertEquals(PlayLock.Action.INSTALLED, PlayLock.action(head + listOf("הסרת התקנה", "פתיחה")))
        assertEquals(PlayLock.Action.INSTALL, PlayLock.action(listOf("Waze", "Install")))
        // "מספר התקנות" / "עודכן ב-" — לא כפתורים
        assertEquals(PlayLock.Action.UNKNOWN, PlayLock.action(listOf("מעל 100 מיליון התקנות", "עודכן ב-3 בנוב׳")))
    }

    @Test
    fun readsTheDownloadPercentage() {
        assertEquals(45, PlayLock.progress(listOf("Waze", "45% מתוך 80MB")))
        assertEquals(7, PlayLock.progress(listOf("7 %")))
        assertNull(PlayLock.progress(listOf("4.5 ★", "התקנה")))
    }

    @Test
    fun clicksInstallAFewTimesAtMostAndNotTooFast() {
        PlayLock.start("com.waze", waze, versionAtStart = null, now = 0)
        val fresh = PlayLock.session!!
        assertTrue(PlayLock.shouldClick(fresh, PlayLock.Action.INSTALL, now = 5_000))
        assertFalse(PlayLock.shouldClick(fresh, PlayLock.Action.INSTALLING, now = 5_000))     // כבר מתקין
        assertFalse(PlayLock.shouldClick(fresh, PlayLock.Action.INSTALLED, now = 5_000))
        PlayLock.noteClick(5_000)
        assertFalse(PlayLock.shouldClick(PlayLock.session!!, PlayLock.Action.INSTALL, now = 6_000))   // מוקדם מדי
        assertTrue(PlayLock.shouldClick(PlayLock.session!!, PlayLock.Action.INSTALL, now = 9_000))
        PlayLock.noteClick(9_000)
        PlayLock.noteClick(13_000)
        assertFalse(PlayLock.shouldClick(PlayLock.session!!, PlayLock.Action.UPDATE, now = 60_000))  // 3 פעמים — מספיק
    }

    @Test
    fun installIsDoneWhenTheInstalledVersionChanges() {
        PlayLock.start("com.waze", waze, versionAtStart = 10, now = 0)
        val s = PlayLock.session!!
        assertFalse(PlayLock.installedDuring(s, 10))
        assertFalse(PlayLock.installedDuring(s, null))
        assertTrue(PlayLock.installedDuring(s, 11))
        assertEquals(waze.first(), s.label)
    }

    @Test
    fun stuckWhenNothingHappensForAWhileAndRetryStartsOver() {
        PlayLock.start("com.waze", waze, versionAtStart = null, now = 1_000)
        val s = PlayLock.session!!
        assertFalse(PlayLock.stuck(s, lastProgressAt = 0, now = 1_000 + PlayLock.STUCK_MS - 1))
        assertTrue(PlayLock.stuck(s, lastProgressAt = 0, now = 1_000 + PlayLock.STUCK_MS))
        assertFalse(PlayLock.stuck(s, lastProgressAt = 30_000, now = 50_000))        // הורדה התקדמה לא מזמן
        PlayLock.noteClick(10_000); PlayLock.noteClick(20_000); PlayLock.noteClick(30_000)
        PlayLock.retry(100_000)
        val r = PlayLock.session!!
        assertEquals(0, r.clicks)
        assertFalse(PlayLock.stuck(r, lastProgressAt = 0, now = 100_000 + PlayLock.STUCK_MS - 1))
        assertTrue(PlayLock.active(100_000 + PlayLock.WINDOW_MS / 2 - 1) != null)
    }

    @Test
    fun targetPageIsRecognisedOnlyByTheHeader() {
        assertEquals(true, PlayLock.isTargetPage(listOf("ניווט למעלה", "Waze: ניווט ועדכוני תנועה", "התקנה"), titles))
        assertEquals(false, PlayLock.isTargetPage(listOf("מוביט", "התקנה"), titles))
        val far = List(PlayLock.HEAD_TEXTS) { "פסקה $it" } + listOf("Waze: ניווט ועדכוני תנועה")
        assertEquals(false, PlayLock.isTargetPage(far, titles))
    }

    @Test
    fun targetPageMatchesWhenPlayTitleIsShorterThanLabel() {
        // הכותרת ב-Play קצרה משם האפליקציה שבטלפון ("waze" מול "waze navigation")
        assertEquals(true, PlayLock.isTargetPage(listOf("waze"), listOf("waze navigation")))
    }
}
