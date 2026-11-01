package com.filterphone.app

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DataProbeTunnelTest {
    @Test
    fun buildsAStandardDnsQuery() {
        val q = DataProbe.dnsQuery(0x1234, "filterphone.com")
        // header: id, RD, 1 question
        assertArrayEquals(byteArrayOf(0x12, 0x34, 0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0), q.copyOfRange(0, 12))
        // 11 "filterphone" 3 "com" 0, type A, class IN
        assertEquals(11, q[12].toInt())
        assertEquals("filterphone", String(q.copyOfRange(13, 24), Charsets.US_ASCII))
        assertEquals(3, q[24].toInt())
        assertArrayEquals(byteArrayOf(0, 0, 1, 0, 1), q.copyOfRange(q.size - 5, q.size))
    }

    @Test
    fun readsTheAnswerId() {
        val answer = ByteArray(40).also { it[0] = 0x12; it[1] = 0x34 }
        assertEquals(0x1234, DataProbe.answerId(answer, 40))
        assertEquals(-1, DataProbe.answerId(answer, 5))
    }

    @Test
    fun cannotTestWithoutAPrivateTunnelDns() {
        assertNull(DataProbe.viaTunnel(null))
        assertNull(DataProbe.viaTunnel("not-an-ip"))
        assertNull(DataProbe.viaTunnel("1.1.1.1"))          // שרת ציבורי — מגיעים אליו גם בלי המנהרה
    }
}
