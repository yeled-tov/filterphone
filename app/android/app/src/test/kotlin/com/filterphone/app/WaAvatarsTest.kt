package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** תמונות פרופיל בוואטסאפ: מה מכוסה בלבן (רשימה, כותרת, פרטים) ומה לא (תמונות בצ'אט, צפייה בגדול). */
class WaAvatarsTest {
    private val cfg = WaAvatars.Config()
    private val screen = 1080L * 2400
    private fun box(l: Int, t: Int, s: Int) = WaAvatars.Box(l, t, l + s, t + s)
    private val list = WaAvatars.Box(0, 300, 1080, 2200, round = false)

    @Test
    fun chatListAvatarsAreMaskedAsCirclesAndAsColumnsWhileScrolling() {
        val rows = (0 until 6).map { i ->
            WaAvatars.Node("com.whatsapp:id/contact_photo", "android.widget.ImageView", null, box(930, 320 + i * 180, 132), list)
        }
        val still = WaAvatars.masks(rows.asSequence(), cfg, screen, 40, 6, scrolling = false, stripMaxPx = 200)
        assertEquals(6, still.size)
        assertTrue(still.all { it.round })
        assertEquals(WaAvatars.Box(924, 314, 1068, 458), still[0])
        // בגלילה: פס אחד לכל גובה הרשימה בעמודה של התמונות (לא מציץ בין עדכון לעדכון)
        val scrolling = WaAvatars.masks(rows.asSequence(), cfg, screen, 40, 6, scrolling = true, stripMaxPx = 200)
        assertEquals(1, scrolling.size)
        assertFalse(scrolling[0].round)
        assertEquals(300, scrolling[0].top)
        assertEquals(2200, scrolling[0].bottom)
        assertTrue(scrolling[0].left <= 924 && scrolling[0].right >= 1068)
    }

    @Test
    fun headerAndDescriptionsAreMaskedButChatMediaAndFullScreenAreNot() {
        val header = WaAvatars.Node("com.whatsapp:id/conversation_contact_photo", "android.widget.ImageView", null, box(120, 80, 100))
        val described = WaAvatars.Node(null, "android.widget.ImageView", "תמונת פרופיל", box(400, 900, 300))
        val media = WaAvatars.Node("com.whatsapp:id/image", "android.widget.ImageView", null, box(100, 1000, 600))
        val wide = WaAvatars.Node("com.whatsapp:id/contact_photo", "android.widget.ImageView", null, WaAvatars.Box(0, 0, 1000, 300))
        val big = WaAvatars.Node("com.whatsapp:id/profile_picture", "android.widget.ImageView", null, box(0, 400, 1080))
        val tiny = WaAvatars.Node("com.whatsapp:id/contact_photo", "android.widget.ImageView", null, box(10, 10, 20))
        val out = WaAvatars.masks(sequenceOf(header, described, media, wide, big, tiny), cfg, screen, 40, 0,
            scrolling = false, stripMaxPx = 200)
        assertEquals(listOf(header.box, described.box), out)
    }

    @Test
    fun serverListDecidesTheIdsAndSquareImageIdsAreCollectedForCalibration() {
        val custom = WaAvatars.Config(ids = listOf("new_avatar_id"))
        val n = WaAvatars.Node("com.whatsapp:id/new_avatar_id", "android.widget.ImageView", null, box(0, 0, 100))
        assertTrue(WaAvatars.isAvatar(n, custom, screen, 40))
        assertFalse(WaAvatars.isAvatar(n.copy(viewId = "com.whatsapp:id/contact_photo"), custom, screen, 40))
        val ids = WaAvatars.squareImageIds(sequenceOf(n,
            WaAvatars.Node("com.whatsapp:id/banner", "android.widget.ImageView", null, WaAvatars.Box(0, 0, 900, 200)),
            WaAvatars.Node("com.whatsapp:id/text", "android.widget.TextView", null, box(0, 0, 100))), 40)
        assertEquals(setOf("new_avatar_id"), ids)
    }
}
