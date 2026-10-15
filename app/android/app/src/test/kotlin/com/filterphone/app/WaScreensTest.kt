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
}
