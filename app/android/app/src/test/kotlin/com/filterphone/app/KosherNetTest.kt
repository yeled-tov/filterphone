package com.filterphone.app

import com.filterphone.app.KosherNet.Step
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class KosherNetTest {
    private val bundled by lazy { KosherNet.parsePem(File("src/main/res/raw/kosher_netfree.pem").readText()) }
    private val fake by lazy {
        KosherNet.parsePem(javaClass.getResourceAsStream("/kosher/fake_netfree_chain.pem")!!.bufferedReader().readText())
    }

    /** טביעות SHA-256 של תעודות השורש הרשמיות (https://netfree.link/cacert/united/x2/ ו-/united/). */
    private val official = setOf(
        "22d61b27b728865bdd8cf46eba5677d2a9de0ef059236b67c1e438abe6bb33bc",   // X2 RSA
        "17031b612d4bc0db25de1999f167f2e6dbac181f638606849aa45ea1f13ffc40",   // X2 ED25519
        "336a31840c0500fe32fac5759d30610dd66f5a5d8e606f0ef2eba3e218c73c06",   // X2 ECC256
        "fce033c1057ee152163251bc1ef9852c3f7ae813ab873ba18de46b6e3763afb7",   // X2 ECC384
        "ae1efd860d72348e8b6a65fa2531359e86d4b26ae87fad510adc818e67e9fb71",   // X2 RSA 2037
        "a5390fd33cea846dc02c34f618accb02c18a6a4e1963695a665635163275b76b",   // RSA
        "6a0442c5deb560c0902533395b33d9faab4f73c80f87f398db9c81cec7db8a32",   // ED25519
        "230783e003fc5e57aa61d52edd3b764ad5ff9378636e60e194501eb33cee1671",   // ECC256
        "de34092171b2a077ce9ca0d72c201a8061067b86d251570e2a991deaa67538b6",   // ECC384
        "2227fbd982918c9e9540c823453a08ed15ff0ed3f69523d1df4f8c985f267c92",   // RSA 2037
    )

    @Test
    fun bundledRootsAreExactlyTheOfficialNetFreeSet() {
        assertEquals(10, bundled.size)
        val fps = bundled.map { KosherNet.fingerprint(it) }.toSet()
        assertEquals(10, fps.size)
        assertEquals(official, fps)
        for (c in bundled) {
            assertTrue(c.subjectX500Principal.name, c.subjectX500Principal.name.contains("O=NetFree"))
            assertTrue("CA", c.basicConstraints >= 0)
            assertEquals("self-signed root", c.subjectX500Principal, c.issuerX500Principal)
        }
    }

    @Test
    fun aChainWithTheSameNameButAnotherKeyIsNotNetFree() {
        assertEquals(2, fake.size)
        val (leaf, fakeRoot) = fake
        // אותו שם בדיוק כמו תעודת השורש האמיתית
        assertTrue(bundled.any { it.subjectX500Principal == fakeRoot.subjectX500Principal })
        val netfree = listOf(KosherNet.Provider("netfree", bundled))
        assertNull(KosherNet.signedBy(arrayOf(leaf, fakeRoot), "RSA", netfree))
        assertNull(KosherNet.signedBy(arrayOf(leaf), "RSA", netfree))
        // בקרה: מול ספק שבאמת חתם — כן מזוהה (בדיקת PKIX עובדת)
        val signer = listOf(KosherNet.Provider("netfree", bundled), KosherNet.Provider("test", listOf(fakeRoot)))
        assertEquals("test", KosherNet.signedBy(arrayOf(leaf, fakeRoot), "RSA", signer))
        assertEquals("test", KosherNet.signedBy(arrayOf(leaf), "RSA", signer))
    }

    @Test
    fun issuerHintNamesTheInterceptor() {
        val (leaf, _) = fake
        assertEquals("NetFree · NetFree Root CA X2 1990-2200 RSA", KosherNet.issuerHint(arrayOf(leaf)))
        assertNull(KosherNet.issuerHint(null))
    }

    @Test
    fun pemParserSkipsCommentsAndGarbage() {
        val one = File("src/main/res/raw/kosher_netfree.pem").readText()
            .substringAfter("\n").let { "# הערה\nזבל\n" + it.substringBefore("-----END CERTIFICATE-----") + "-----END CERTIFICATE-----\n" }
        assertEquals(1, KosherNet.parsePem(one).size)
        assertEquals(0, KosherNet.parsePem("-----BEGIN CERTIFICATE-----\nnot base64!!\n-----END CERTIFICATE-----").size)
        assertEquals(0, KosherNet.parsePem("").size)
    }

    @Test
    fun decide() {
        // כבוי מהדשבורד / אין רשת — יוצאים אם היינו במצב
        assertEquals(Step.EXIT, KosherNet.decide(false, 5L, 5L, true, false))
        assertEquals(Step.NONE, KosherNet.decide(false, null, 5L, true, false))
        assertEquals(Step.EXIT, KosherNet.decide(true, 5L, null, null, false))
        // רשת אחרת — מיד, בלי לחכות לבדיקה
        assertEquals(Step.EXIT, KosherNet.decide(true, 5L, 6L, null, false))
        assertEquals(Step.EXIT, KosherNet.decide(true, 5L, 6L, true, false))
        // אותה רשת: נשארים (גם כשהבדיקה לא הגיעה לאינטרנט), יוצאים רק כשהיא כבר לא כשרה
        assertEquals(Step.STAY, KosherNet.decide(true, 5L, 5L, null, false))
        assertEquals(Step.STAY, KosherNet.decide(true, 5L, 5L, true, false))
        assertEquals(Step.EXIT, KosherNet.decide(true, 5L, 5L, false, false))
        // רשת חדשה: נכנסים רק כשהיא כשרה וה-VPN שלנו לא עובד בה
        assertEquals(Step.ENTER, KosherNet.decide(true, null, 5L, true, false))
        assertEquals(Step.NONE, KosherNet.decide(true, null, 5L, true, true))
        assertEquals(Step.NONE, KosherNet.decide(true, null, 5L, false, false))
        assertEquals(Step.NONE, KosherNet.decide(true, null, 5L, null, false))
    }
}
