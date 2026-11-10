package com.filterphone.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WaChatsTest {
    private val h = 2000                       // גובה מסך: כותרת עד 400, פס טאבים מ-1500

    private fun header(text: String? = null, id: String? = null, desc: String? = null) =
        WaChats.Node(id, text, desc, top = 120, bottom = 220)

    private fun body(id: String? = null, text: String? = null) = WaChats.Node(id, text, null, top = 800, bottom = 900)
    private fun entry() = WaChats.Node("com.whatsapp:id/entry", null, null, top = 1850, bottom = 1950, editable = true)
    private fun tab(text: String) = WaChats.Node(null, text, null, top = 1880, bottom = 1990)
    private val name = header("משה כהן", "com.whatsapp:id/conversation_contact_name")

    @Test
    fun privateChatIsPrivate() {
        val nodes = listOf(name, header("נראה/תה לאחרונה היום ב-12:30", "com.whatsapp:id/conversation_contact_status"),
            body("com.whatsapp:id/message_text"), entry())
        assertEquals(WaChats.Kind.PRIVATE, WaChats.classify(nodes, h))
    }

    @Test
    fun channelIsDetectedByFollowersOrIdEvenWithoutTheScreenName() {
        assertEquals(WaChats.Kind.CHANNEL,
            WaChats.classify(listOf(header("חדשות היום"), header("12.3K עוקבים"), body()), h))
        assertEquals(WaChats.Kind.CHANNEL,
            WaChats.classify(listOf(header("Daily News"), header("Channel · 1,204 followers")), h))
        assertEquals(WaChats.Kind.CHANNEL,
            WaChats.classify(listOf(header("חדשות"), header("עקוב")), h))                     // כפתור "עקוב"
        assertEquals(WaChats.Kind.CHANNEL,
            WaChats.classify(listOf(header("חדשות", "com.whatsapp:id/newsletter_title")), h))
        // מזהה "ערוץ" בגוף המסך, בלי שורת כתיבה — צופה בערוץ
        assertEquals(WaChats.Kind.CHANNEL,
            WaChats.classify(listOf(header("חדשות"), body("com.whatsapp:id/newsletter_reactions")), h))
    }

    @Test
    fun forwardedChannelMessageInsidePrivateChatIsNotAChannel() {
        // בצ'אט רגיל הודעה שהועברה מערוץ ("צפייה בערוץ") — יש שורת כתיבה: לא מעיפים מהצ'אט
        val nodes = listOf(name, body("com.whatsapp:id/newsletter_forward_view"), entry())
        assertEquals(WaChats.Kind.PRIVATE, WaChats.classify(nodes, h))
    }

    @Test
    fun groupIsDetectedBySenderNamesOrHeader() {
        assertEquals(WaChats.Kind.GROUP, WaChats.classify(listOf(header("משפחה"),
            body("com.whatsapp:id/name_in_group"), entry()), h))
        assertEquals(WaChats.Kind.GROUP, WaChats.classify(listOf(header("משפחה"),
            header("הקש/י כאן למידע על הקבוצה"), entry()), h))
        assertEquals(WaChats.Kind.GROUP, WaChats.classify(listOf(header("משפחה"),
            header("משה, דוד, את/ה"), entry()), h))
        assertEquals(WaChats.Kind.GROUP, WaChats.classify(listOf(header("Family"),
            header("Moshe, David, Yossi"), entry()), h))
        assertEquals(WaChats.Kind.GROUP, WaChats.classify(listOf(header("Family"),
            header("tap here for group info"), entry()), h))
    }

    @Test
    fun homeScreenWithTabsIsNeverAChatEvenWithFollowersOrSenderNames() {
        // טאב העדכונים: ערוצים מוצעים עם "עוקבים" — מסך הבית, לא "בתוך ערוץ"
        val nodes = listOf(header("עדכונים"), body(text = "12K עוקבים"), body("com.whatsapp:id/newsletter_item"),
            tab("צ'אטים"), tab("עדכונים"), tab("קהילות"), tab("שיחות"))
        assertEquals(WaChats.Kind.HOME, WaChats.classify(nodes, h))
    }

    @Test
    fun messageTextFarFromTheHeaderIsIgnored() {
        // הודעה בגוף הצ'אט עם "1000 עוקבים" / פסיקים — לא נקראת (רק הכותרת)
        val nodes = listOf(name, body(text = "יש לו 1000 עוקבים, משה, דוד, את/ה"), entry())
        assertEquals(WaChats.Kind.PRIVATE, WaChats.classify(nodes, h))
    }

    @Test
    fun helpers() {
        assertTrue(WaChats.isFollowers("3 אלף עוקבים"))
        assertFalse(WaChats.isFollowers("עוקבים"))                       // בלי מספר
        assertFalse(WaChats.isFollowers("עוקביםשלי 3"))
        assertTrue(WaChats.isParticipants("משה, את/ה"))
        assertFalse(WaChats.isParticipants("משה, דוד"))                  // שני שמות בלי "את/ה" — אולי שם איש קשר
        assertFalse(WaChats.isParticipants("מחובר/ת"))
        assertEquals(WaChats.Kind.UNKNOWN, WaChats.classify(emptyList(), h))
        assertEquals(WaChats.Kind.UNKNOWN, WaChats.classify(listOf(name), 0))
    }

    @Test
    fun signalsComeFromTheServerWithDefaultsForMissingFields() {
        val s = WaChats.parse(JSONObject("""{"group_ids": ["sender_name"], "follower_words": []}"""))
        assertEquals(listOf("sender_name"), s.groupIds)
        assertEquals(WaChats.DEFAULT.followerWords, s.followerWords)    // ריק → ברירת המחדל
        assertEquals(WaChats.DEFAULT, WaChats.parse(null))
        assertEquals(WaChats.Kind.GROUP,
            WaChats.classify(listOf(header("משפחה"), body("com.whatsapp:id/sender_name_tv"), entry()), h, s))
    }
}
