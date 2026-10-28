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
        assertNull(SafeSites.toUrl("זמני תפילה", SafeSites.ESSENTIAL))                    // בלי חיפוש באינטרנט
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
}
