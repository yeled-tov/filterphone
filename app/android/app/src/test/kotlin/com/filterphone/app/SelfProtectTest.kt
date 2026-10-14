package com.filterphone.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SelfProtectTest {
    private val label = "FilterPhone"

    @Test
    fun blocksFilterPhoneAppInfoWithForceStop() {
        // דף פרטי האפליקציה של FilterPhone (עברית, אנדרואיד 16)
        assertTrue(SelfProtect.isCriticalScreen(
            listOf("FilterPhone", "אילוץ עצירה", "הסרת ההתקנה", "אחסון", "התראות"), label))
        assertTrue(SelfProtect.isCriticalScreen(
            listOf("FilterPhone", "Force stop", "Uninstall", "Storage"), label))
        // ניקוי נתונים/אחסון
        assertTrue(SelfProtect.isCriticalScreen(listOf("FilterPhone", "ניקוי האחסון", "מחיקת נתונים"), label))
    }

    @Test
    fun blocksDeviceAdminDeactivationForFilterPhone() {
        assertTrue(SelfProtect.isCriticalScreen(
            listOf("FilterPhone", "השבתה של אפליקציית ניהול המכשיר"), label))
        assertTrue(SelfProtect.isCriticalScreen(listOf("FilterPhone", "Deactivate this device admin app"), label))
    }

    @Test
    fun doesNotBlockTheAppsListOrOtherApps() {
        // רשימת האפליקציות: FilterPhone מופיע, אבל אין פעולה הרסנית — לא מקפיצים
        assertFalse(SelfProtect.isCriticalScreen(
            listOf("אפליקציות", "FilterPhone", "WhatsApp", "Chrome", "גלקסי סטור"), label))
        // דף פרטי אפליקציה של אפליקציה *אחרת* (בלי השם שלנו) — לא נוגעים
        assertFalse(SelfProtect.isCriticalScreen(listOf("WhatsApp", "אילוץ עצירה", "הסרת ההתקנה"), label))
        // מסך הגדרות כללי
        assertFalse(SelfProtect.isCriticalScreen(listOf("רשת ואינטרנט", "מסך", "סוללה"), label))
    }

    @Test
    fun emptyLabelOrTextsIsSafe() {
        assertFalse(SelfProtect.isCriticalScreen(listOf("אילוץ עצירה"), ""))
        assertFalse(SelfProtect.isCriticalScreen(emptyList(), label))
    }
}
