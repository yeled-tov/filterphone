package com.filterphone.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SelfUpdateTest {
    private val sha = "ab".repeat(32)

    private fun file(code: Long, abis: List<String>, url: String = "https://api.filterphone.com/download/F.apk",
                     hash: String = sha, size: Long = 30_000_000) =
        JSONObject().put("version_code", code).put("url", url).put("sha256", hash).put("size", size)
            .put("abis", JSONArray(abis))

    private fun update(vararg files: JSONObject) =
        JSONObject().put("version_name", "1.0.8").put("files", JSONArray(files.toList()))

    @Test
    fun picksTheNewestFileForThisProcessor() {
        val u = update(file(2008, listOf("arm64-v8a")), file(8, listOf("arm64-v8a", "armeabi-v7a", "x86_64")))
        val arm64 = SelfUpdate.pick(u, installed = 7, abis = listOf("arm64-v8a", "armeabi-v7a"))!!
        assertEquals(2008, arm64.versionCode)
        assertEquals("1.0.8", arm64.versionName)
        val old32bit = SelfUpdate.pick(u, installed = 7, abis = listOf("armeabi-v7a"))!!
        assertEquals(8, old32bit.versionCode)        // arm64 בלבד לא מתאים לטלפון 32 ביט
    }

    @Test
    fun sameVersionCodeLightFileFirstGoesToArm64AndUniversalTo32Bit() {
        // כמו ש-FilterPhone.json מתפרסם: הקל (arm64) ראשון, האוניברסלי אחריו — אותו versionCode
        val light = "https://api.filterphone.com/download/FilterPhone.apk"
        val universal = "https://api.filterphone.com/download/FilterPhone-universal.apk"
        val u = update(file(3101, listOf("arm64-v8a"), url = light),
                       file(3101, listOf("arm64-v8a", "armeabi-v7a", "x86_64"), url = universal))
        assertEquals(light, SelfUpdate.pick(u, installed = 3095, abis = listOf("arm64-v8a", "armeabi-v7a"))!!.url)
        assertEquals(universal, SelfUpdate.pick(u, installed = 3095, abis = listOf("armeabi-v7a", "armeabi"))!!.url)
        // אותו קוד שכבר מותקן (אוניברסלי או קל) — לא "מתעדכנים" ביניהם סתם
        assertNull(SelfUpdate.pick(u, installed = 3101, abis = listOf("arm64-v8a")))
    }

    @Test
    fun neverDowngradesOrReinstalls() {
        val u = update(file(2008, listOf("arm64-v8a")), file(8, emptyList()))
        assertNull(SelfUpdate.pick(u, installed = 2008, abis = listOf("arm64-v8a")))
        // מותקן arm64 (2007): הגרסה לכל המעבדים (8) היא "ישנה יותר" לפי versionCode — לא מוצעת
        assertEquals(2008, SelfUpdate.pick(u, installed = 2007, abis = listOf("arm64-v8a"))!!.versionCode)
        assertNull(SelfUpdate.pick(update(file(8, emptyList())), installed = 2007, abis = listOf("arm64-v8a")))
    }

    @Test
    fun rejectsUnsafeEntries() {
        val abis = listOf("arm64-v8a")
        assertNull(SelfUpdate.pick(update(file(9, abis, url = "http://api.filterphone.com/F.apk")), 8, abis))
        assertNull(SelfUpdate.pick(update(file(9, abis, hash = "not-a-hash")), 8, abis))
        assertNull(SelfUpdate.pick(update(file(9, abis, size = SelfUpdate.MAX_BYTES + 1)), 8, abis))
        assertNull(SelfUpdate.pick(update(file(9, abis, size = 0)), 8, abis))
        assertNull(SelfUpdate.pick(update(file(9, listOf("x86_64"))), 8, abis))
        assertNull(SelfUpdate.pick(null, 8, abis))
        assertNull(SelfUpdate.pick(JSONObject().put("files", "garbage"), 8, abis))
        // SHA-256 באותיות גדולות — תקין (מנורמל)
        assertEquals(sha, SelfUpdate.pick(update(file(9, abis, hash = sha.uppercase())), 8, abis)!!.sha256)
    }

    @Test
    fun signerMustContinueTheInstalledOne() {
        val mine = setOf("aa")
        assertTrue(SelfUpdate.sameSigner(mine, setOf("aa"), setOf("aa")))
        assertTrue(SelfUpdate.sameSigner(mine, setOf("bb"), setOf("aa", "bb")))     // רוטציית מפתח רשמית
        assertFalse(SelfUpdate.sameSigner(mine, setOf("bb"), setOf("bb")))          // מפתח אחר
        assertFalse(SelfUpdate.sameSigner(emptySet(), setOf("aa"), setOf("aa")))
    }
}
