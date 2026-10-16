package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DataProbeTest {
    @Test
    fun anyHttpAnswerMeansTrafficFlows() {
        assertNull(DataProbe.parseStatus("HTTP/1.1 204 No Content"))
        assertNull(DataProbe.parseStatus("HTTP/1.1 200 OK"))
        assertNull(DataProbe.parseStatus("HTTP/1.1 308 Permanent Redirect"))   // האתר שלנו מפנה ל-HTTPS
        assertNull(DataProbe.parseStatus("HTTP/1.1 403 Forbidden"))            // דף חסימה של הפרוקסי — עדיין עובר
        assertNull(DataProbe.parseStatus("HTTP/1.0 204 No Content\r"))
    }

    @Test
    fun noOrGarbledAnswerIsAFailure() {
        assertEquals("http:bad", DataProbe.parseStatus(null))
        assertEquals("http:bad", DataProbe.parseStatus(""))
        assertEquals("http:bad", DataProbe.parseStatus("SSH-2.0-OpenSSH"))
        assertEquals("http:bad", DataProbe.parseStatus("HTTP/1.1 abc"))
        assertEquals("http:bad", DataProbe.parseStatus("HTTP/1.1 999 Nope"))
    }

    @Test
    fun requestIsAPlainHttpGetWithHost() {
        val r = DataProbe.request("filterphone.com")
        assertTrue(r.startsWith("GET /generate_204 HTTP/1.1\r\n"))
        assertTrue(r.contains("\r\nHost: filterphone.com\r\n"))
        assertTrue(r.endsWith("\r\n\r\n"))
    }

    @Test
    fun ourOwnSiteIsTriedFirst() {
        assertEquals("filterphone.com", DataProbe.TARGETS.first())
    }
}
