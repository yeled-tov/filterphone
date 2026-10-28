package com.filterphone.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** כלי Root: זיהוי חלון בקשת הרשאת-על (su) — כדי לדחות אותו (לא לאשר גישת-על). */
class RootGuardTest {
    @Test
    fun recognizesTheSuRequestWindowByItsScreenName() {
        assertTrue(RootGuard.looksLikeSuWindow("com.topjohnwu.magisk.core.SuRequestActivity"))
        assertTrue(RootGuard.looksLikeSuWindow("me.weishu.kernelsu.ui.SuperUserActivity"))
        assertFalse(RootGuard.looksLikeSuWindow("com.topjohnwu.magisk.MainActivity"))
        assertFalse(RootGuard.looksLikeSuWindow(null))
    }

    @Test
    fun recognizesTheSuRequestByItsText() {
        assertTrue(RootGuard.looksLikeSuText(listOf("Superuser Request", "Shell", "Grant", "Deny")))
        assertTrue(RootGuard.looksLikeSuText(listOf("‏בקשת הרשאת-על", "אשר", "דחה")))
        assertTrue(RootGuard.looksLikeSuText(listOf("אפליקציה מבקשת גישה", "הענק superuser", "ביטול")))
        // "אשר" לבד או "root" לבד — לא מספיק (למנוע חסימת דיאלוגים לגיטימיים)
        assertFalse(RootGuard.looksLikeSuText(listOf("אשר את הפעולה", "המשך")))
        assertFalse(RootGuard.looksLikeSuText(listOf("root folder", "open")))
        assertFalse(RootGuard.looksLikeSuText(emptyList()))
    }

    @Test
    fun isSuPromptCombinesPackageScreenAndText() {
        // חלון su — לפי שם המסך, בלי תלות בטקסט
        assertTrue(RootGuard.isSuPrompt("com.topjohnwu.magisk", "x.SuRequestActivity", emptyList()))
        // מנהל Root שמציג טקסט בקשה
        assertTrue(RootGuard.isSuPrompt("me.weishu.kernelsu", "x.Main", listOf("Superuser Request", "Grant")))
        // המסך הראשי של המנהל (בלי חלון אישור) — לא נתפס כאן (ייחסם כאפליקציה רגילה)
        assertFalse(RootGuard.isSuPrompt("com.topjohnwu.magisk", "x.MainActivity", listOf("Magisk", "Modules")))
        // FilterPhone עצמה — לעולם לא
        assertFalse(RootGuard.isSuPrompt("com.filterphone.app", "x.SuRequestActivity", listOf("Superuser Request")))
    }

    @Test
    fun theKnownManagersAreListed() {
        for (p in listOf("com.topjohnwu.magisk", "me.weishu.kernelsu", "me.bmax.apatch", "bin.mt.plus",
                         "com.dimonvideo.luckypatcher")) {
            assertTrue(p, p in RootGuard.MANAGERS)
        }
    }
}
