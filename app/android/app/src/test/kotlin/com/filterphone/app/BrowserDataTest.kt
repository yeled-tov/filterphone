package com.filterphone.app

import com.filterphone.app.BrowserData.Page
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** מה שהסייר זוכר: מוצמדים, היסטוריה, כרטיסיות. */
class BrowserDataTest {
    @Test
    fun pinsHaveNoDuplicatesAndAreCapped() {
        val a = Page("ביטוח לאומי", "https://www.btl.gov.il")
        var pins = BrowserData.withPin(emptyList(), a)
        pins = BrowserData.withPin(pins, a.copy(url = "http://btl.gov.il/"))            // אותו אתר
        assertEquals(1, pins.size)
        pins = BrowserData.withoutPin(pins, "https://btl.gov.il")
        assertTrue(pins.isEmpty())
        val many = (1..20).fold(emptyList<Page>()) { acc, i -> BrowserData.withPin(acc, Page("$i", "https://s$i.gov.il")) }
        assertEquals(BrowserData.MAX_PINS, many.size)
        assertEquals("https://s20.gov.il", many.last().url)
        assertFalse(BrowserData.DEFAULT_PINS.isEmpty())
        assertTrue(BrowserData.DEFAULT_PINS.all { SafeSites.allows(SafeSites.ESSENTIAL, SafeSites.hostOf(it.url)) })
    }

    @Test
    fun historyKeepsTheLatestVisitOnTop() {
        var h = BrowserData.withVisit(emptyList(), Page("א", "https://www.gov.il", 1))
        h = BrowserData.withVisit(h, Page("ב", "https://www.btl.gov.il", 2))
        h = BrowserData.withVisit(h, Page("א", "https://www.gov.il/", 3))                // שוב — למעלה, בלי כפילות
        assertEquals(listOf("https://www.gov.il/", "https://www.btl.gov.il"), h.map { it.url })
        assertEquals(h, BrowserData.withVisit(h, Page("x", "about:blank")))              // לא דף אינטרנט
        val long = (1..400).fold(emptyList<Page>()) { acc, i -> BrowserData.withVisit(acc, Page("$i", "https://s$i.gov.il")) }
        assertEquals(BrowserData.MAX_HISTORY, long.size)
    }

    @Test
    fun pagesSurviveJson() {
        val pages = listOf(Page("שירותי ממשלה", "https://www.gov.il/he", 5), Page("", "https://my.gov.il"))
        assertEquals(pages, BrowserData.decode(BrowserData.encode(pages)))
        assertEquals(emptyList<Page>(), BrowserData.decode("not json"))
        assertEquals(1, BrowserData.decode("""[{"t":"x","u":""},{"t":"y","u":"https://a.gov.il"}]""").size)
    }
}
