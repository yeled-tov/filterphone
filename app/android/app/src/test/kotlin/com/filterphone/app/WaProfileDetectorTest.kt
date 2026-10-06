package com.filterphone.app

import com.filterphone.app.WaProfileDetector.Node
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** זיהוי צפייה בתמונת פרופיל בוואטסאפ — שכבות, ובלי חסימות שווא (תמונות בצ'אט, אייקונים קטנים). */
class WaProfileDetectorTest {
    private val d = WaProfileDetector(GuardPolicy.WaDetector.DEFAULT)

    private fun img(id: String?, area: Float, desc: String? = null) =
        Node(viewId = id, className = "android.widget.ImageView", description = desc, areaFraction = area)

    @Test
    fun layer1KnownViewerActivities() {
        assertEquals("activity:viewprofilephoto", d.matchWindow("com.whatsapp", "com.whatsapp.profile.ViewProfilePhoto"))
        assertEquals("activity:quickcontact", d.matchWindow("com.whatsapp.w4b", "com.whatsapp.QuickContactActivity"))
        assertNull(d.matchWindow("com.whatsapp", "com.whatsapp.Conversation"))
        assertNull(d.matchWindow("org.telegram.messenger", "ViewProfilePhoto"))      // רק וואטסאפ
    }

    @Test
    fun layer2LargeProfileImageById() {
        val nodes = sequenceOf(img("com.whatsapp:id/toolbar", 0.1f), img("com.whatsapp:id/profile_picture", 0.6f))
        assertEquals("view_id:profile_picture", d.matchNodes(nodes))
    }

    @Test
    fun smallAvatarsInChatListAreNotAViewer() {
        val list = (1..20).map { img("com.whatsapp:id/contact_photo", 0.004f) }.asSequence()
        assertNull(d.matchNodes(list))
    }

    @Test
    fun layer3DescriptionOnlyOnImages() {
        assertEquals("description", d.matchNodes(sequenceOf(img(null, 0.5f, "Profile photo"))))
        assertEquals("description", d.matchNodes(sequenceOf(img(null, 0.5f, "תמונת פרופיל"))))
        // טקסט רגיל (לא תמונה) עם אותן מילים — לא נבדק בכלל
        val text = Node("com.whatsapp:id/message_text", "android.widget.TextView", "profile photo", 0.5f)
        assertNull(d.matchNodes(sequenceOf(text)))
    }

    @Test
    fun genericChatMediaIdsDoNotMatch() {
        // תמונה גדולה בצ'אט / במציג המדיה — מזהה כללי, לא "פרופיל"
        assertNull(d.matchNodes(sequenceOf(img("com.whatsapp:id/image", 0.8f), img("com.whatsapp:id/picture", 0.8f))))
    }

    @Test
    fun excludedWindowsNeverCount() {
        assertTrue(d.excluded("com.whatsapp.mediaview.MediaViewActivity"))
        assertTrue(d.excluded("com.whatsapp.gallery.GalleryPicker"))
        assertFalse(d.excluded("com.whatsapp.profile.ViewProfilePhoto"))
        assertFalse(d.excluded(null))
    }

    @Test
    fun serverCanExtendPatterns() {
        val custom = GuardPolicy.parseStrict(
            """{"protections":{"wa_profile_photo":{"view_ids":["new_avatar_viewer"],"min_area":0.2}}}"""
        ).waProfile!!
        val d2 = WaProfileDetector(custom)
        assertEquals("view_id:new_avatar_viewer", d2.matchNodes(sequenceOf(img("com.whatsapp:id/new_avatar_viewer", 0.25f))))
        // תבניות שלא נשלחו — ברירת המחדל
        assertEquals(GuardPolicy.WaDetector.DEFAULT.activities, custom.activities)
    }
}
