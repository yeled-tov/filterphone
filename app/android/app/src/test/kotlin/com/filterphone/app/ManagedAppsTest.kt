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
    fun autoUpdatesOnlyInstalledStoreAppsWithANewerHostedVersion() {
        val list = JSONArray(listOf(
            app(pkg = "com.filtertube.app", required = false, versionCode = 20),   // מותקנת בגרסה 19 → עדכון
            app(pkg = "same.version", required = false, versionCode = 5),          // מעודכנת
            app(pkg = "not.installed", required = false, versionCode = 3),         // לא מתקינים חדשות
            app(pkg = "via.play", required = false, url = "", versionCode = 9),    // לא מתארחת אצלנו
        ))
        val installed = mapOf("com.filtertube.app" to 19L, "same.version" to 5L, "via.play" to 1L)
        assertEquals(listOf("com.filtertube.app"), ManagedApps.updatable(list) { installed[it] }.map { it.pkg })
        assertTrue(ManagedApps.updatable(null) { 1L }.isEmpty())
    }

    @Test
    fun storeUpdateNoticeKeyIsStablePerVersion() {
        val a = ManagedApps.App("b.pkg", "https://x/a.apk", sha, 3)
        val b = ManagedApps.App("a.pkg", "https://x/b.apk", sha, 7)
        assertEquals("a.pkg:7,b.pkg:3", StoreUpdateNotify.key(listOf(a, b)))
        assertEquals(StoreUpdateNotify.key(listOf(a, b)), StoreUpdateNotify.key(listOf(b, a)))
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
