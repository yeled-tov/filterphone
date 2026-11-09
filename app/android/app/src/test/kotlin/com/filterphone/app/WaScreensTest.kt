package com.filterphone.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WaScreensTest {
    private fun n(text: String? = null, desc: String? = null, selected: Boolean = false) =
        WaScreens.Node(text, desc, selected)

    @Test
    fun updatesTabSelectedIsDetectedInHebrewAndEnglish() {
        val home = sequenceOf(n("צ'אטים"), n("עדכונים", selected = true), n("קהילות"), n("שיחות"))
        assertTrue(WaScreens.updatesSelected(home))
        assertTrue(WaScreens.updatesSelected(sequenceOf(n(desc = "Updates, 3 new", selected = true))))
        assertTrue(WaScreens.updatesSelected(sequenceOf(n("STATUS", selected = true))))       // גרסה ישנה
    }

    @Test
    fun chatsTabSelectedOrUnselectedLabelsDoNotTrigger() {
        // הטאב "עדכונים" קיים אבל לא נבחר — אנחנו בצ'אטים
        assertFalse(WaScreens.updatesSelected(sequenceOf(n("צ'אטים", selected = true), n("עדכונים"))))
        // צ'אט / סטטוס של איש קשר שבשם שלו יש את המילה — לא טאב
        assertFalse(WaScreens.updatesSelected(sequenceOf(n("עדכונים מהגבאי", selected = true))))
        assertFalse(WaScreens.updatesSelected(sequenceOf(n("סטטוסים של משה", selected = true))))
        assertFalse(WaScreens.updatesSelected(emptySequence()))
    }

    @Test
    fun chatsTabIsFoundWithAnyApostropheButNotCalls() {
        assertTrue(WaScreens.isChatsTab("צ'אטים", null))
        assertTrue(WaScreens.isChatsTab("צ׳אטים", null))
        assertTrue(WaScreens.isChatsTab(null, "Chats"))
        assertFalse(WaScreens.isChatsTab("שיחות", null))          // בעברית — שיחות קוליות
        assertFalse(WaScreens.isChatsTab("Calls", null))
    }

    @Test
    fun channelsAndStatusHeadersInTheUpdatesTab() {
        assertTrue(WaScreens.isChannelsHeader("ערוצים"))
        assertTrue(WaScreens.isChannelsHeader("Channels"))
        assertFalse(WaScreens.isChannelsHeader("חיפוש ערוצים"))     // רק הכותרת עצמה
        assertTrue(WaScreens.isStatusHeader("סטטוס"))
        assertTrue(WaScreens.isStatusHeader("Status"))
        assertFalse(WaScreens.isStatusHeader("ערוצים"))
    }

    @Test
    fun scrollUpTargetsTheVerticalListNotTheTabPager() {
        // ה-ViewPager של הטאבים: "אחורה" בו מחליף טאב (לצ'אטים/לשיחות) — לעולם לא הוא
        assertFalse(WaScreens.isVerticalList("androidx.viewpager2.widget.ViewPager2", supportsScrollUp = false))
        assertFalse(WaScreens.isVerticalList("androidx.viewpager.widget.ViewPager", supportsScrollUp = true))
        assertFalse(WaScreens.isVerticalList("android.widget.HorizontalScrollView", supportsScrollUp = false))
        // הרשימה של הסטטוסים/הערוצים
        assertTrue(WaScreens.isVerticalList("androidx.recyclerview.widget.RecyclerView", supportsScrollUp = false))
        assertTrue(WaScreens.isVerticalList("android.widget.ListView", supportsScrollUp = false))
        assertTrue(WaScreens.isVerticalList("android.view.View", supportsScrollUp = true))   // מצהיר "גלילה למעלה"
        // קרוסלת הסטטוסים האופקית (RecyclerView שמצהיר רק ימינה/שמאלה) — לא
        assertFalse(WaScreens.isVerticalList("androidx.recyclerview.widget.RecyclerView", false, horizontalOnly = true))
        assertFalse(WaScreens.isVerticalList(null, supportsScrollUp = false))
    }
}
