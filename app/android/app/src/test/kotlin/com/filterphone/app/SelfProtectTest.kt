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

    @Test
    fun otherAppsInfoPagesStayOpenEvenIfTheyMentionUs() {
        // דף פרטים של אפליקציה שהותקנה דרך החנות שלנו: "הותקנה מ-FilterPhone" — זה לא הדף שלנו
        assertFalse(SelfProtect.isCriticalScreen(
            listOf("WhatsApp", "אילוץ עצירה", "הסרת ההתקנה", "פרטי האפליקציה", "האפליקציה הותקנה מ-FilterPhone"), label))
        assertFalse(SelfProtect.isCriticalScreen(
            listOf("Waze", "Force stop", "App details", "App installed from FilterPhone"), label))
        // רשימת מנהלי המכשיר: FilterPhone ברשימה, בלי כפתור השבתה — אפשר להיכנס לרשימה
        assertFalse(SelfProtect.isCriticalScreen(
            listOf("אפליקציות לניהול המכשיר", "FilterPhone", "Find My Device", "Google Pay"), label))
    }

    @Test
    fun coversMoreWordingsAndLateLoadedTitles() {
        // סמסונג: "עצור בכפייה"; אנדרואיד 15+: "העברה לארכיון"; "השבתת" (ולא רק "השבתה")
        assertTrue(SelfProtect.isCriticalScreen(listOf("FilterPhone", "עצור בכפייה"), label))
        assertTrue(SelfProtect.isCriticalScreen(listOf("FilterPhone", "העברה לארכיון"), label))
        assertTrue(SelfProtect.isCriticalScreen(listOf("FilterPhone", "השבתת אפליקציית מנהל המכשיר"), label))
        // הכותרת עם גרסה באותה שורה, בראש המסך; תווי כיווניות לא מפריעים
        assertTrue(SelfProtect.isCriticalScreen(listOf("\u200fFilterPhone 2.4.1", "Force stop"), label))
    }

    @Test
    fun longPressOnOurIconOnly() {
        assertTrue(SelfProtect.isOurIcon(listOf("FilterPhone"), label))
        assertTrue(SelfProtect.isOurIcon(listOf("FilterPhone, 2 התראות"), label))
        assertFalse(SelfProtect.isOurIcon(listOf("WhatsApp"), label))
        assertFalse(SelfProtect.isOurIcon(listOf("FilterTube"), label))
        assertFalse(SelfProtect.isOurIcon(emptyList(), label))
        assertFalse(SelfProtect.isOurIcon(listOf("FilterPhone"), ""))
    }

    @Test
    fun recognisesTheLauncherMenu() {
        assertTrue(SelfProtect.isLauncherMenu(listOf("FilterPhone", "מידע על האפליקציה", "השהיית האפליקציה")))
        assertTrue(SelfProtect.isLauncherMenu(listOf("App info", "Pause app", "Widgets")))
        // מסך הבית בלי תפריט פתוח
        assertFalse(SelfProtect.isLauncherMenu(listOf("FilterPhone", "WhatsApp", "טלפון", "מצלמה")))
    }
}
