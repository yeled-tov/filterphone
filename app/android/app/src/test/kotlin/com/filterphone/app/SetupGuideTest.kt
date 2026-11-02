package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Test

class SetupGuideTest {
    @Test
    fun xiaomiSecurityDebuggingPropertyIsOnOffOrUnknown() {
        assertEquals(true, SetupGuide.securityAdb("1"))
        assertEquals(false, SetupGuide.securityAdb("0"))
        assertEquals(null, SetupGuide.securityAdb(""))          // המערכת לא נותנת לקרוא
        assertEquals(null, SetupGuide.securityAdb(null))
    }

    @Test
    fun vendorFamiliesForTheBuildNumberHint() {
        assertEquals("samsung", SetupGuide.vendor("samsung", "samsung"))
        assertEquals("xiaomi", SetupGuide.vendor("Xiaomi", "Redmi"))
        assertEquals("xiaomi", SetupGuide.vendor("Xiaomi", "POCO"))
        assertEquals("oneplus", SetupGuide.vendor("OnePlus", "OnePlus"))
        assertEquals("oneplus", SetupGuide.vendor("OPPO", "realme"))
        assertEquals("pixel", SetupGuide.vendor("Google", "google"))
        assertEquals("other", SetupGuide.vendor("ZTE", "nubia"))
    }
}
