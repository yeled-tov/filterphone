package com.filterphone.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class VpnBypassTest {
    private val cert = "ab".repeat(32)
    private val other = "cd".repeat(32)

    @Test
    fun missingFieldFallsBackToBothFilterTubeVariants() {
        assertEquals(listOf("com.filtertube.app", "com.filtertube.filtertube"), VpnBypass.parse(null).map { it.pkg })
        assertEquals(VpnBypass.DEFAULTS, GuardPolicy.parse("""{"blocked_apps": []}""").vpnBypass)
    }

    @Test
    fun disabledMeansNothingIsExcluded() {
        assertTrue(VpnBypass.parse(JSONObject().put("enabled", false)).isEmpty())
    }

    @Test
    fun parsesServerListAndSkipsBadEntries() {
        val raw = JSONObject().put("enabled", true).put("apps", JSONArray(listOf(
            JSONObject().put("package", "com.filtertube.app").put("certs", JSONArray(listOf(cert.uppercase(), "bad"))),
            JSONObject().put("package", "not a package"),
            JSONObject().put("package", "org.example.safe"),
        )))
        val apps = VpnBypass.parse(raw)
        assertEquals(listOf("com.filtertube.app", "org.example.safe"), apps.map { it.pkg })
        assertEquals(setOf(cert), apps[0].certs)
    }

    @Test
    fun onlyInstalledAppsWithMatchingSignatureAreExcluded() {
        val apps = listOf(VpnBypass.App("com.filtertube.app", setOf(cert)), VpnBypass.App("com.filtertube.filtertube"),
            VpnBypass.App("not.installed"), VpnBypass.App("com.filterphone.app"))
        val installed = mapOf(
            "com.filtertube.app" to listOf(other),              // מותקנת בחתימה אחרת — נשארת בסינון
            "com.filtertube.filtertube" to listOf(other),       // בלי נעילה — לפי שם
            "com.filterphone.app" to listOf(cert),
        )
        val d = VpnBypass.decide(apps, installed, self = "com.filterphone.app")
        assertEquals(setOf("com.filtertube.filtertube"), d.excluded)
        assertEquals(setOf("com.filtertube.app"), d.mismatch)
        assertEquals(mapOf("com.filtertube.app" to other, "com.filtertube.filtertube" to other), d.certs)
    }

    @Test
    fun rotatedKeyWithProvenLineageStillMatches() {
        val d = VpnBypass.decide(listOf(VpnBypass.App("com.filtertube.app", setOf(cert))),
            mapOf("com.filtertube.app" to listOf(cert, other)), self = "x.y")
        assertEquals(setOf("com.filtertube.app"), d.excluded)
        assertEquals(other, d.certs["com.filtertube.app"])          // החתימה הנוכחית מדווחת
    }

    @Test
    fun legacyTextConfigGetsTheExclusionLine() {
        val raw = "[Interface]\nPrivateKey = x\nAddress = 10.8.0.2/32\n\n[Peer]\nPublicKey = y\n"
        val out = VpnConfigFactory.withExcluded(raw, setOf("com.filtertube.app"))
        assertTrue(out.contains("[Interface]\nExcludedApplications = com.filtertube.app\nPrivateKey"))
        val merged = VpnConfigFactory.withExcluded(out, setOf("com.filtertube.app", "com.filtertube.filtertube"))
        assertTrue(merged.contains("ExcludedApplications = com.filtertube.app, com.filtertube.filtertube"))
        assertEquals(raw, VpnConfigFactory.withExcluded(raw, emptySet()))
        assertEquals("garbage", VpnConfigFactory.withExcluded("garbage", setOf("a.b")))
    }
}
