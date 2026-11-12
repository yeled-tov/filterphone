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
        assertTrue(still.all { it.round && it.follow && it.clip == list })   // זזים עם הרשימה, חתוכים לגבולותיה
        assertEquals(WaAvatars.Box(924, 314, 1068, 458, clip = list, follow = true), still[0])
        // "זריקה" מהירה: פס אחד לכל גובה הרשימה בעמודה של התמונות
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

    @Test
    fun avatarCutAtTheListEdgeIsStillCoveredAsAFullCircleClippedToTheList() {
        // רק 40 פיקסלים תחתונים של התמונה גלויים בראש הרשימה (השאר מתחת לכותרת) — פעם לא כוסה בכלל (לא ריבועי)
        val cut = WaAvatars.Node("com.whatsapp:id/contact_photo", "android.widget.ImageView", null,
            WaAvatars.Box(930, 300, 1062, 340), list)
        assertEquals(WaAvatars.Box(930, 208, 1062, 340), WaAvatars.fullBox(cut, 200))
        val out = WaAvatars.masks(sequenceOf(cut), cfg, screen, 40, 6, scrolling = false, stripMaxPx = 200)
        assertEquals(listOf(WaAvatars.Box(924, 202, 1068, 346, clip = list, follow = true)), out)
        // בתחתית הרשימה (נכנסת מלמטה)
        val bottom = cut.copy(box = WaAvatars.Box(930, 2150, 1062, 2200))
        assertEquals(WaAvatars.Box(930, 2150, 1062, 2282), WaAvatars.fullBox(bottom, 200))
        // תמונה רחבה בתוך רשימה (תצוגת קישור) לא "מושלמת" לריבוע ענק
        val wide = cut.copy(box = WaAvatars.Box(100, 300, 1000, 500))
        assertFalse(WaAvatars.isAvatar(wide, cfg, screen, 40, 200))
    }

    @Test
    fun infoScreenCoversTheBigHeaderPhotoEvenWhenItShrinksOnScroll() {
        val info = WaAvatars.Config(maxFraction = 0.7f, bannerMinWidth = 540)
        val header = WaAvatars.Node("com.whatsapp:id/profile_picture_image", "android.widget.ImageView", null,
            WaAvatars.Box(0, 0, 1080, 1080))
        val out = WaAvatars.masks(sequenceOf(header), info, screen, 40, 0, scrolling = false, stripMaxPx = 200)
        assertEquals(listOf(WaAvatars.Box(0, 0, 1080, 1080, round = false)), out)        // גדולה — מלבן
        // בגלילה התמונה מתכווצת לפס רחב — עדיין מכוסה
        val shrunk = header.copy(box = WaAvatars.Box(0, 0, 1080, 320))
        assertTrue(WaAvatars.isAvatar(shrunk, info, screen, 40, 200))
        assertFalse(WaAvatars.isAvatar(shrunk, cfg, screen, 40, 200))                  // לא במסך פרטים
        // תמונה רחבה בלי מזהה / תיאור של פרופיל (קטלוג, מדיה) — לא נוגעים
        assertFalse(WaAvatars.isAvatar(header.copy(viewId = "com.whatsapp:id/catalog_image", box = shrunk.box),
            info, screen, 40, 200))
    }

    @Test
    fun scrollSpeedComesFromTheSameRowsBetweenTwoMeasurements() {
        fun row(key: Int, top: Int) = WaAvatars.Node("com.whatsapp:id/contact_photo", "android.widget.ImageView", null,
            box(930, top, 132), list, key)
        val before = listOf(row(1, 500), row(2, 680), row(3, 860))
        // הרשימה עלתה 90 פיקסלים; שורה 1 יצאה ונכנסה שורה חדשה (4) — לא משנה
        val after = listOf(row(2, 590), row(3, 770), row(4, 950))
        assertEquals(0 to -90, WaAvatars.shift(before, after))
        assertEquals(null, WaAvatars.shift(before, listOf(row(9, 500))))             // אין שורה משותפת
        // שורה שנחתכה בקצה הרשימה לא נחשבת (המיקום שלה הוא רק החלק הגלוי)
        val clipped = listOf(row(2, 590), row(3, 770).copy(box = WaAvatars.Box(930, 2150, 1062, 2200)))
        assertEquals(0 to -90, WaAvatars.shift(before, clipped))
    }
}
