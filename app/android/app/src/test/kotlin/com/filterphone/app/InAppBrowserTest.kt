package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class InAppBrowserTest {
    private fun n(cls: String? = null, text: String? = null) = InAppBrowser.Node(cls, text)

    @Test
    fun addressBarUrlIsOnlyAWholeUrlNotAnySentenceWithADot() {
        assertEquals("https://example.com/page", InAppBrowser.addressBarUrl("https://example.com/page"))
        assertEquals("https://www.ynet.co.il", InAppBrowser.addressBarUrl("www.ynet.co.il"))     // בלי https
        assertEquals("https://shop.example.com/a/b", InAppBrowser.addressBarUrl("shop.example.com/a/b"))
        assertNull(InAppBrowser.addressBarUrl("בקרו באתר שלנו example.com עכשיו"))                // משפט — לא שורת כתובת
        assertNull(InAppBrowser.addressBarUrl("שלום לכולם"))
        assertNull(InAppBrowser.addressBarUrl("4.5 כוכבים"))
        assertNull(InAppBrowser.addressBarUrl(null))
    }

    @Test
    fun detectsInAppBrowserByWebViewPlusAddressBar() {
        val browser = sequenceOf(
            n("android.widget.FrameLayout"),
            n("android.widget.EditText", "https://news.example.com/article"),
            n("android.webkit.WebView"),
        )
        assertEquals("https://news.example.com/article", InAppBrowser.detect(browser))
    }

    @Test
    fun webViewWithoutAnAddressBarIsStillBlockedButWithoutAUrl() {
        val noBar = sequenceOf(n("android.webkit.WebView"), n("android.widget.TextView", "טוען…"))
        assertEquals("", InAppBrowser.detect(noBar))         // דפדפן בלי כתובת קריאה — עדיין נחסם
    }

    @Test
    fun embeddedWebContentWithoutAWebViewNodeIsNotTreatedAsABrowser() {
        // תווית עם קישור בלבד, בלי WebView — לא דפדפן (למשל קישור בפרופיל)
        val label = sequenceOf(n("android.widget.TextView", "https://example.com"))
        assertNull(InAppBrowser.detect(label))
        // מסך רגיל
        assertNull(InAppBrowser.detect(sequenceOf(n("android.widget.Button", "המשך"), n(text = "ברוכים הבאים"))))
    }
}
