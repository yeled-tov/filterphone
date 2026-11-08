package com.filterphone.app

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UiRulesTest {
    private fun rules(json: String) = UiRules.parse(JSONArray(json))

    @Test
    fun parsesWindowAndNodeRulesAndSkipsBadOnes() {
        val list = rules(
            """[
              {"id":"ai_screen","packages":["com.whatsapp"],"activities":["MetaAiActivity","aichat"],"action":"back"},
              {"id":"ai_button","packages":["com.whatsapp"],"view_ids":["ai_fab"],"descriptions":["Meta AI","מטא AI"],"action":"cover"},
              {"id":"iab","activities":["IABWebCoreActivity"],"action":"home","message":"הדפדפן הפנימי חסום"},
              {"no":"id"},
              {"id":"empty"}
            ]""",
        )
        assertEquals(listOf("ai_screen", "ai_button", "iab"), list.map { it.id })   // ללא id / ללא בוחרים — מדולגים
        val screen = list[0]
        assertTrue(screen.isWindow)
        assertEquals(UiRules.Action.BACK, screen.action)
        val button = list[1]
        assertTrue(button.isNode && !button.isWindow)
        assertEquals(UiRules.Action.COVER, button.action)
        assertEquals(UiRules.Action.HOME, list[2].action)
        assertEquals("הדפדפן הפנימי חסום", list[2].message)
    }

    @Test
    fun appliesToPackageOrAnyWhenEmpty() {
        val anyApp = rules("""[{"id":"x","activities":["Foo"]}]""")[0]
        assertTrue(UiRules.appliesTo(anyApp, "com.anything"))
        assertTrue(UiRules.appliesTo(anyApp, null))
        val waOnly = rules("""[{"id":"x","packages":["com.whatsapp"],"activities":["Foo"]}]""")[0]
        assertTrue(UiRules.appliesTo(waOnly, "com.whatsapp"))
        assertFalse(UiRules.appliesTo(waOnly, "com.other"))
        assertFalse(UiRules.appliesTo(waOnly, null))
    }

    @Test
    fun windowMatchesBySubstringCaseInsensitive() {
        val rule = rules("""[{"id":"x","activities":["MetaAiActivity","aichat"]}]""")[0]
        assertTrue(UiRules.matchesWindow(rule, "com.whatsapp.metaai.MetaAiActivity"))
        assertTrue(UiRules.matchesWindow(rule, "com.whatsapp.AiChatHome"))         // case-insensitive
        assertFalse(UiRules.matchesWindow(rule, "com.whatsapp.Conversation"))
        assertFalse(UiRules.matchesWindow(rule, null))
    }

    @Test
    fun nodeMatchesByViewIdDescriptionOrText() {
        val rule = rules(
            """[{"id":"x","view_ids":["ai_fab"],"descriptions":["Meta AI","מטא AI"],"texts":["שאל את מטא"]}]""",
        )[0]
        assertTrue(UiRules.matchesNode(rule, "com.whatsapp:id/ai_fab", null, null))
        assertTrue(UiRules.matchesNode(rule, null, "Meta AI", null))
        assertTrue(UiRules.matchesNode(rule, null, "מטא AI", null))               // עם סימני כיוון/רווחים
        assertTrue(UiRules.matchesNode(rule, null, null, "שאל את מטא"))
        assertFalse(UiRules.matchesNode(rule, "com.whatsapp:id/entry", "הקלדת הודעה", "שלום"))
        assertFalse(UiRules.matchesNode(rule, null, null, null))
    }

    @Test
    fun capsRuleCount() {
        val many = (0 until 200).joinToString(",") { """{"id":"r$it","activities":["A"]}""" }
        assertEquals(UiRules.MAX_RULES, rules("[$many]").size)
    }

    @Test
    fun emptyOrNonArrayIsEmpty() {
        assertTrue(UiRules.parse(null).isEmpty())
        assertTrue(UiRules.parse("not an array").isEmpty())
        assertNull(UiRules.parse(JSONArray("[]")).firstOrNull())
    }
}
