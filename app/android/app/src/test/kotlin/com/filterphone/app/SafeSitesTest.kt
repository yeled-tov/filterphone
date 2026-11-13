package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** הסייר המוגן: מצב לפי המדיניות, מה מותר באתרים חיוניים, ושורת הכתובת. */
class SafeSitesTest {
    @Test
    fun modeFollowsTheServerAndNeverOpensBrowsingWhenChromeIsBlocked() {
        assertEquals(SafeSites.FULL, SafeSites.mode(SafeSites.FULL, chromeBlocked = false))
        assertEquals(SafeSites.OFF, SafeSites.mode(SafeSites.FULL, chromeBlocked = true))      // שרת ישן בלי השדה
        assertEquals(SafeSites.ESSENTIAL, SafeSites.mode(SafeSites.ESSENTIAL, chromeBlocked = true))
        assertEquals(SafeSites.OFF, SafeSites.mode(SafeSites.OFF, chromeBlocked = false))
    }

    @Test
    fun essentialModeAllowsOnlyTheListedSitesAndTheirSubdomains() {
        assertTrue(SafeSites.allows(SafeSites.ESSENTIAL, "www.btl.gov.il"))
        assertTrue(SafeSites.allows(SafeSites.ESSENTIAL, "login.gov.il"))
        assertTrue(SafeSites.allows(SafeSites.ESSENTIAL, "hb2.bankleumi.co.il"))
        assertTrue(SafeSites.allows(SafeSites.ESSENTIAL, "WWW.CLALIT.CO.IL."))
        assertFalse(SafeSites.allows(SafeSites.ESSENTIAL, "www.google.com"))
        assertFalse(SafeSites.allows(SafeSites.ESSENTIAL, "evilgov.il"))                   // לא תת-דומיין של gov.il
        assertFalse(SafeSites.allows(SafeSites.ESSENTIAL, "gov.il.evil.com"))
        assertFalse(SafeSites.allows(SafeSites.ESSENTIAL, null))
        assertTrue(SafeSites.allows(SafeSites.FULL, "www.google.com"))
        assertFalse(SafeSites.allows(SafeSites.OFF, "www.gov.il"))
        assertTrue(SafeSites.SITES.all { SafeSites.allows(SafeSites.ESSENTIAL, SafeSites.hostOf(it.url)) })
    }

    @Test
    fun addressBarTurnsTextIntoAUrlOrASearch() {
        assertEquals("https://www.gov.il/he", SafeSites.toUrl("https://www.gov.il/he", SafeSites.FULL))
        assertEquals("https://gov.il", SafeSites.toUrl("gov.il", SafeSites.ESSENTIAL))
        assertEquals("https://my.gov.il/x?y=1", SafeSites.toUrl(" my.gov.il/x?y=1 ", SafeSites.FULL))
        assertEquals("https://www.google.com/search?safe=active&q=%D7%96%D7%9E%D7%A0%D7%99+%D7%AA%D7%A4%D7%99%D7%9C%D7%94",
            SafeSites.toUrl("זמני תפילה", SafeSites.FULL))
        // באתרים מאושרים בלבד — חיפוש, אבל רק בתוך האתרים המאושרים
        val essential = SafeSites.toUrl("זמני תפילה", SafeSites.ESSENTIAL)!!
        assertTrue(essential.startsWith("https://www.google.com/search?safe=active&q="))
        assertTrue(java.net.URLDecoder.decode(essential, "UTF-8").contains("site:gov.il"))
        assertNull(SafeSites.toUrl("זמני תפילה", SafeSites.OFF))
        assertNull(SafeSites.toUrl("   ", SafeSites.FULL))
        assertNull(SafeSites.toUrl("https://", SafeSites.FULL))
    }

    @Test
    fun hostAndExternalSchemes() {
        assertEquals("www.leumi.co.il", SafeSites.hostOf("https://user@www.Leumi.co.il:443/שלום?x"))
        assertNull(SafeSites.hostOf("not a url"))
        assertTrue(SafeSites.isExternalScheme("tel"))
        assertTrue(SafeSites.isExternalScheme("mailto"))
        assertFalse(SafeSites.isExternalScheme("intent"))
        assertFalse(SafeSites.isExternalScheme("javascript"))
        assertFalse(SafeSites.isExternalScheme(null))
    }

    private fun query(url: String) = java.net.URLDecoder.decode(url.substringAfter("q=").substringBefore("&"), "UTF-8")

    @Test
    fun smartSearchLooksOnlyInsideTheApprovedSitesThatFitTheQuestion() {
        // שאלה כללית ("חידוש תו נכה") — ממשלה ושירותים + קופות חולים
        val gov = query(SafeSites.searchUrl("חידוש תו נכה", SafeSites.ESSENTIAL)!!)
        assertTrue(gov.startsWith("חידוש תו נכה ("))
        assertTrue(gov.contains("site:gov.il") && gov.contains("site:clalit.co.il"))
        assertFalse(gov.contains("site:bankhapoalim.co.il"))
        // בנק / אשראי — לפי המילים
        assertEquals(listOf("בנקים"), SafeSites.searchGroups("איך פותחים חשבון בבנק"))
        assertEquals(listOf("כרטיסי אשראי"), SafeSites.searchGroups("ביטול כרטיס ישראכרט"))
        assertEquals(listOf("בנקים", "כרטיסי אשראי"), SafeSites.searchGroups("הלוואה בכרטיס אשראי"))
        assertEquals(listOf("קופות חולים ובריאות"), SafeSites.searchGroups("תור לרופא משפחה"))
        assertTrue(SafeSites.searchGroups("מד\"א").contains("קופות חולים ובריאות"))       // מירכאות לא משנות
        // גוגל קורא ~32 מילים — לא יותר מ-12 אתרים בחיפוש
        assertTrue(SafeSites.searchDomains("הלוואה בכרטיס אשראי בבנק").size <= 12)
        assertEquals("https://www.google.com/search?safe=active&q=%D7%A9%D7%9C%D7%95%D7%9D",
            SafeSites.searchUrl("שלום", SafeSites.FULL))
        assertNull(SafeSites.searchUrl("שלום", SafeSites.OFF))
        assertNull(SafeSites.searchUrl("  ", SafeSites.ESSENTIAL))
    }

    @Test
    fun googleResultsAreAllowedOnlyAsARestrictedSearch() {
        val search = SafeSites.searchUrl("חידוש תו נכה", SafeSites.ESSENTIAL)!!
        assertTrue(SafeSites.allowsUrl(SafeSites.ESSENTIAL, search))
        assertNull(SafeSites.restrictSearch(search, SafeSites.ESSENTIAL))               // כבר מוגבל — בסדר
        // חיפוש חדש מתוך דף התוצאות (בלי ההגבלה) — מתוקן להיות שוב רק באתרים המאושרים
        val fixed = SafeSites.restrictSearch("https://www.google.com/search?q=%D7%97%D7%93%D7%A9%D7%95%D7%AA&hl=he",
            SafeSites.ESSENTIAL)!!
        assertTrue(query(fixed).startsWith("חדשות (site:"))
        // תמונות / סרטונים — חזרה לתוצאות הרגילות (עם ההגבלה)
        val images = SafeSites.restrictSearch(search + "&udm=2", SafeSites.ESSENTIAL)!!
        assertFalse(images.contains("udm"))
        assertTrue(query(images).contains("site:gov.il"))
        // עמוד הבא בתוצאות (עם פרמטרים נוספים של גוגל) — כבר מוגבל, נשאר כמו שהוא (בלי לולאת הפניות)
        assertNull(SafeSites.restrictSearch("$search&start=10&ei=abc", SafeSites.ESSENTIAL))
        assertTrue(SafeSites.restrictSearch("$search&start=10&tbm=isch", SafeSites.ESSENTIAL)!!.endsWith("&start=10"))
        // שאר גוגל (מפות, חשבון, תמונות) — לא
        assertFalse(SafeSites.allowsUrl(SafeSites.ESSENTIAL, "https://www.google.com/maps?q=x"))
        assertFalse(SafeSites.allowsUrl(SafeSites.ESSENTIAL, "https://accounts.google.com/"))
        assertFalse(SafeSites.allowsUrl(SafeSites.ESSENTIAL, "https://www.youtube.com/"))
        assertTrue(SafeSites.allowsUrl(SafeSites.ESSENTIAL, "https://www.btl.gov.il/benefits"))
        assertFalse(SafeSites.allowsUrl(SafeSites.OFF, search))
        assertTrue(SafeSites.allowsUrl(SafeSites.FULL, "https://example.com"))
        assertNull(SafeSites.restrictSearch("https://www.google.com/search?q=x", SafeSites.FULL))   // גלישה מלאה — כרגיל
        // תוצאה דרך /url?q= — האתר האמיתי
        assertEquals("https://www.gov.il/he/service/x",
            SafeSites.unwrapRedirect("https://www.google.com/url?q=https://www.gov.il/he/service/x&sa=U"))
        assertNull(SafeSites.unwrapRedirect("https://www.google.com/url?q=javascript:alert(1)"))
        assertNull(SafeSites.unwrapRedirect("https://www.btl.gov.il/url?q=https://evil.com"))
    }

    @Test
    fun suggestionsComeFromTheApprovedList() {
        assertEquals("ביטוח לאומי", SafeSites.suggest("ביטוח").first().title)
        assertTrue(SafeSites.suggest("clalit").any { it.title == "כללית" })
        assertTrue(SafeSites.suggest("א").isEmpty())                                    // תו אחד — עוד לא
    }
}
