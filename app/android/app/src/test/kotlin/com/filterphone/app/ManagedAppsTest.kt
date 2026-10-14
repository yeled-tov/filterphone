package com.filterphone.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ManagedAppsTest {
    private val sha = "cd".repeat(32)

    private fun app(
        pkg: String = "org.example.app",
        required: Boolean = true,
        url: String = "https://api.filterphone.com/download/apps/example.apk",
        hash: String = sha,
        versionCode: Long = 12,
    ) = JSONObject().put("package", pkg).put("required", required).put("url", url).put("sha256", hash)
        .put("version_code", versionCode)

    @Test
    fun onlyRequiredSelfHostedVerifiedAppsAreInstallable() {
        val list = JSONArray(listOf(
            app(),
            app(pkg = "optional.app", required = false),
            app(pkg = "plain.http", url = "http://example.com/a.apk"),
            app(pkg = "bad.hash", hash = "1234"),
            app(pkg = "no.version", versionCode = 0),
            JSONObject().put("package", "store.only").put("required", true),
        ))
        val out = ManagedApps.installable(list)
        assertEquals(listOf("org.example.app"), out.map { it.pkg })
        assertEquals(12L, out.single().versionCode)
    }

    @Test
    fun hashIsNormalizedToLowercase() {
        val out = ManagedApps.installable(JSONArray(listOf(app(hash = sha.uppercase()))))
        assertEquals(sha, out.single().sha256)
    }

    @Test
    fun missingOrEmptyCatalogIsSafe() {
        assertTrue(ManagedApps.installable(null).isEmpty())
        assertTrue(ManagedApps.installable(JSONArray()).isEmpty())
    }

    @Test
    fun installsWhenMissingOrOlder() {
        val a = ManagedApps.installable(JSONArray(listOf(app(versionCode = 12)))).single()
        assertTrue(ManagedApps.needsInstall(a, installedVersion = null))
        assertTrue(ManagedApps.needsInstall(a, installedVersion = 11))
        assertFalse(ManagedApps.needsInstall(a, installedVersion = 12))
        assertFalse(ManagedApps.needsInstall(a, installedVersion = 13))   // לא מורידים גרסה
    }

    @Test
    fun storeItemNeedsEverythingForASafeInstall() {
        val ok = ManagedApps.fromStoreItem(app(required = false))
        assertEquals("org.example.app", ok?.pkg)                     // מהחנות — גם בלי "חובה"
        assertEquals(null, ManagedApps.fromStoreItem(app(url = "http://x/a.apk")))
        assertEquals(null, ManagedApps.fromStoreItem(app(hash = "zz")))
        assertEquals(null, ManagedApps.fromStoreItem(JSONObject().put("package", "store.only")))
    }
}
