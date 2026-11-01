package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PlayManagedTest {
    private val approved = listOf("waze: ניווט ועדכוני תנועה", "וייז", "moovit")

    @Test
    fun recognisesInstallButtonsIncludingPaidApps() {
        assertTrue(PlayManaged.isInstallButton("התקנה"))
        assertTrue(PlayManaged.isInstallButton("Install"))
        assertTrue(PlayManaged.isInstallButton("₪4.90"))
        assertTrue(PlayManaged.isInstallButton("$1.99"))
        assertTrue(PlayManaged.isInstallButton("4.90 ₪"))
        assertFalse(PlayManaged.isInstallButton("פתיחה"))
        assertFalse(PlayManaged.isInstallButton("עדכון"))                   // עדכון של מה שכבר מותקן — מותר
        assertFalse(PlayManaged.isInstallButton("מעל 100 מיליון התקנות"))
        assertFalse(PlayManaged.isInstallButton(null))
    }

    @Test
    fun approvedPageOnlyByTheTitleAtTheTop() {
        assertTrue(PlayManaged.pageApproved(listOf("ניווט למעלה", "Waze: ניווט ועדכוני תנועה", "התקנה"), approved))
        assertFalse(PlayManaged.pageApproved(listOf("ניווט למעלה", "TikTok", "התקנה"), approved))
        assertFalse(PlayManaged.pageApproved(listOf("Waze"), emptyList()))   // אין רשימה — לא מאושר
    }

    @Test
    fun guessesTheAppNameForTheRequest() {
        assertEquals("TikTok", PlayManaged.guessTitle(listOf("ניווט למעלה", "חיפוש", "TikTok", "TikTok Pte. Ltd.", "4.4 ★", "התקנה")))
    }

    @Test
    fun blocksOnlyNewUnapprovedInstallsFromPlay() {
        val ok = setOf("com.waze")
        val protected = setOf("com.android.settings")
        assertTrue(PlayManaged.shouldBlockInstall("com.zhiliaoapp.musically", ok, replacing = false,
            installer = "com.android.vending", system = false, protected = protected))
        assertFalse(PlayManaged.shouldBlockInstall("com.waze", ok, false, "com.android.vending", false, protected))      // מאושרת
        assertFalse(PlayManaged.shouldBlockInstall("com.x", ok, replacing = true, "com.android.vending", false, protected)) // עדכון
        assertFalse(PlayManaged.shouldBlockInstall("com.x", ok, false, "com.filterphone.app", false, protected))   // מהחנות שלנו
        assertFalse(PlayManaged.shouldBlockInstall("com.x", ok, false, "com.android.vending", true, protected))    // מערכת
    }
}
