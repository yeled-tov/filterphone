package com.filterphone.app

import org.json.JSONException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** פירוש המדיניות מהשרת: תאימות לאחור, דילוג על רשומות פגומות, ו"לעולם לא נפתח" בגלל JSON שבור. */
class GuardPolicyTest {

    @Test
    fun legacyPolicyFromOldServerStillBlocks() {
        val p = GuardPolicy.parseStrict(
            """{"blocked_apps":["org.mozilla.firefox","com.android.vending"],
               "blocked_screens":[{"package":"com.whatsapp","match":["StatusPlayback"]}]}"""
        )
        assertEquals(setOf("org.mozilla.firefox", "com.android.vending"), p.apps)
        assertEquals(1, p.screens.size)
        assertEquals(0, p.version)
        assertNull(p.waProfile)
    }

    @Test
    fun versionedPolicyWithModesProtectionsAndDpc() {
        val p = GuardPolicy.parseStrict(
            """{"version":7,"checksum":"abc","blocked_apps":["com.example.chat"],
               "app_rules":[{"package":"com.zhiliaoapp.musically","mode":"network"}],
               "protections":{"wa_profile_photo":{"packages":["com.whatsapp"],"activities":["ViewProfilePhoto"],
                 "view_ids":["profile_picture"],"descriptions":["Profile photo"],"min_area":0.4}},
               "dpc":{"block_uninstall":true,"always_on_vpn":false}}"""
        )
        assertEquals(7, p.version)
        assertEquals("abc", p.checksum)
        // "network" לא נחסם בטלפון — רק מה שב-blocked_apps
        assertEquals(setOf("com.example.chat"), p.apps)
        val wa = p.waProfile!!
        assertEquals(setOf("com.whatsapp"), wa.packages)
        assertEquals(listOf("viewprofilephoto"), wa.activities)      // אותיות קטנות להשוואה
        assertEquals(0.4f, wa.minArea, 0.001f)
        assertFalse(p.dpc!!.getBoolean("always_on_vpn"))
    }

    @Test
    fun protectionTrueUsesBuiltInDefaults() {
        val p = GuardPolicy.parseStrict("""{"protections":{"wa_profile_photo":true}}""")
        assertEquals(GuardPolicy.WaDetector.DEFAULT, p.waProfile)
        assertFalse(p.isEmpty)
    }

    @Test
    fun badEntriesAreSkippedNotFatal() {
        val p = GuardPolicy.parseStrict(
            """{"blocked_apps":["ok.app", 5, null, ""],
               "blocked_screens":[{"package":"com.whatsapp"}, "junk", {"package":"x.y","match":["A"]}]}"""
        )
        assertEquals(setOf("ok.app"), p.apps)
        assertEquals(listOf(GuardPolicy.Screen("x.y", listOf("A"))), p.screens)
    }

    @Test
    fun protectedPackagesAreNeverBlocked() {
        val p = GuardPolicy.parseStrict("""{"blocked_apps":["com.android.settings","com.android.systemui","x.y"]}""")
        assertEquals(setOf("x.y"), p.apps)
    }

    @Test(expected = JSONException::class)
    fun brokenJsonIsRejected() {
        GuardPolicy.parseStrict("""{"blocked_apps":["a.b" """)
    }

    @Test(expected = JSONException::class)
    fun wrongTopLevelTypeIsRejected() {
        GuardPolicy.parseStrict("""{"blocked_apps":"com.example"}""")
    }

    @Test
    fun lenientParseNeverThrows() {
        assertTrue(GuardPolicy.parse("not json").isEmpty)
        assertTrue(GuardPolicy.parse(null).isEmpty)
        assertNotNull(GuardPolicy.parse("""{"blocked_apps":["a.b"]}""").apps.firstOrNull())
    }

    @Test
    fun deviceOwnerDefaultsMatchServer() {
        val eff = DeviceOwnerManager.effective(null)
        assertTrue(eff.getValue("block_uninstall"))
        assertTrue(eff.getValue("always_on_vpn"))
        assertFalse(eff.getValue("disallow_debugging"))
        val custom = DeviceOwnerManager.effective(org.json.JSONObject("""{"disallow_debugging":true}"""))
        assertTrue(custom.getValue("disallow_debugging"))
        assertTrue(custom.getValue("block_uninstall"))
    }
}
