package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Test

class CertHelperTest {
    private fun hex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    /** אותו שם קובץ שאנדרואיד (ו-openssl x509 -subject_hash_old) נותנים לתעודה במאגר. */
    @Test
    fun userStoreFileNameMatchesOpenSsl() {
        val subject = hex("302f31143012060355040a0c0b46696c74657250686f6e65" +
            "3117301506035504030c0e46696c74657250686f6e65204341")      // O=FilterPhone, CN=FilterPhone CA
        assertEquals("2b0b70bf", CertHelper.subjectHashOld(subject))
    }
}

class DeviceIdTest {
    @Test
    fun stableHashedAndScopedToThePackage() {
        val a = DeviceId.of("9774d56d682e549c", "com.filterphone.app")
        assertEquals(32, a.length)
        assertEquals(a, DeviceId.of("9774d56d682e549c", "com.filterphone.app"))
        org.junit.Assert.assertNotEquals(a, DeviceId.of("9774d56d682e549c", "other.app"))
        org.junit.Assert.assertFalse(a.contains("9774d56d682e549c"))
    }
}
