package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ChromeReportTest {
    @Test
    fun recognisesChromesLongPressMenuForLinksAndImages() {
        val link = listOf("כתבה על משהו", "www.example.co.il/news/1", "פתיחה בכרטיסייה חדשה",
            "פתיחה בכרטיסייה חדשה בגלישה בסתר", "העתקת כתובת הקישור", "העתקת טקסט הקישור", "הורדת הקישור")
        assertEquals(ChromeReport.Kind.LINK, ChromeReport.contextMenu(link))
        assertEquals("https://www.example.co.il/news/1", ChromeReport.menuUrl(link))
        val image = listOf("photo.jpg", "https://cdn.example.com/a/photo.jpg?w=600", "Open image in new tab",
            "Download image", "Share image")
        assertEquals(ChromeReport.Kind.IMAGE, ChromeReport.contextMenu(image))
        assertEquals("https://cdn.example.com/a/photo.jpg?w=600", ChromeReport.menuUrl(image))
        // דף רגיל — לא תפריט (גם אם יש בו מילה אחת דומה)
        assertNull(ChromeReport.contextMenu(listOf("חדשות", "פתיחה בכרטיסייה חדשה של המדריך")))
    }

    @Test
    fun readsThePageUrlFromTheAddressBar() {
        assertEquals("https://ynet.co.il/news/article/abc", ChromeReport.pageUrl("ynet.co.il/news/article/abc"))
        assertEquals("http://example.com/", ChromeReport.pageUrl("http://example.com/"))
        assertNull(ChromeReport.pageUrl("מתכון עוגת שוקולד"))        // חיפוש שמוקלד — לא כתובת
        assertNull(ChromeReport.pageUrl(""))
    }
}
