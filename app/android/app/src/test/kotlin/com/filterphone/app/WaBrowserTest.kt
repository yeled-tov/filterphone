package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** הדפדפן הפנימי של וואטסאפ: זיהוי המסך, כפתור ⋮ והפריט "פתיחה בדפדפן" — בעברית ובאנגלית. */
class WaBrowserTest {
    @Test
    fun recognizesTheInAppBrowserScreensOnly() {
        assertTrue(WaBrowser.isBrowserScreen("com.whatsapp.iab.IABWebCoreActivity"))
        assertTrue(WaBrowser.isBrowserScreen("com.whatsapp.iab.watchandbrowse.WatchAndBrowseActivity"))
        assertFalse(WaBrowser.isBrowserScreen("com.whatsapp.Conversation"))
        assertFalse(WaBrowser.isBrowserScreen(null))
    }

    @Test
    fun findsTheMenuAndTheOpenInBrowserItem() {
        assertTrue(WaBrowser.isMoreMenu(null, "אפשרויות נוספות"))
        assertTrue(WaBrowser.isMoreMenu(null, "More options"))
        assertFalse(WaBrowser.isMoreMenu("WhatsApp", "w.meta.me"))
        assertTrue(WaBrowser.isOpenExternal("פתיחה בדפדפן", null))
        assertTrue(WaBrowser.isOpenExternal("פתיחה ב-Chrome", null))
        assertTrue(WaBrowser.isOpenExternal("Open in browser", null))
        assertTrue(WaBrowser.isOpenExternal("Open in external browser", null))
        assertFalse(WaBrowser.isOpenExternal("העתקת הקישור", null))
        assertFalse(WaBrowser.isOpenExternal("הגדרות", null))
        assertFalse(WaBrowser.isOpenExternal("Refresh", null))
    }

    @Test
    fun moreVariantsOfTheOpenInBrowserItem() {
        assertTrue(WaBrowser.isOpenExternal("פתיחה באמצעות הדפדפן", null))
        assertTrue(WaBrowser.isOpenExternal("לפתוח בכרום", null))
        assertTrue(WaBrowser.isOpenExternal(null, "Open in Chrome"))
        assertTrue(WaBrowser.isOpenExternal("View in browser", null))
        assertTrue(WaBrowser.isOpenExternal("פתיחה בדפדפן חיצוני", null))
        assertTrue(WaBrowser.isMoreMenu(null, "עוד"))
        assertFalse(WaBrowser.isMoreMenu("עוד הודעות מהעסק", null))
    }

    @Test
    fun copyLinkIsTheFallbackItem() {
        assertTrue(WaBrowser.isCopyLink("העתקת הקישור", null))
        assertTrue(WaBrowser.isCopyLink("Copy link", null))
        assertFalse(WaBrowser.isCopyLink("פתיחה בדפדפן", null))
        assertFalse(WaBrowser.isCopyLink("שיתוף", null))
    }

    @Test
    fun theChatComposerMeansTheBrowserIsNotUpYet() {
        assertTrue(WaBrowser.isComposer("com.whatsapp:id/entry"))
        assertTrue(WaBrowser.isComposer("com.whatsapp.w4b:id/entry"))
        assertFalse(WaBrowser.isComposer("com.whatsapp:id/entry_container"))
        assertFalse(WaBrowser.isComposer(null))
    }

    @Test
    fun linkFromTheClipboardIsHttpOnlyAndTrimmed() {
        assertEquals("https://w.meta.me/s/abc", WaBrowser.linkFrom("https://w.meta.me/s/abc"))
        assertEquals("https://example.co.il/a?b=1", WaBrowser.linkFrom("ראו כאן: https://example.co.il/a?b=1."))
        assertNull(WaBrowser.linkFrom("intent://evil#Intent;end"))
        assertNull(WaBrowser.linkFrom("javascript:alert(1)"))
        assertNull(WaBrowser.linkFrom("בלי קישור"))
        assertNull(WaBrowser.linkFrom(null))
    }

    @Test
    fun diagnosticSummaryIsShortLabelsOnly() {
        val long = "x".repeat(60)
        assertEquals("רענון|שיתוף", WaBrowser.summary(listOf("רענון", "שיתוף", "רענון", long, " ")))
        assertTrue(WaBrowser.summary(List(20) { "תווית $it" }).length <= 70)
    }
}
