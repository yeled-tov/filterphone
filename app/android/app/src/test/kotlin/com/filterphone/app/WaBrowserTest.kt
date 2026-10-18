package com.filterphone.app

import org.junit.Assert.assertFalse
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
}
