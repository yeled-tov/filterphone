package com.filterphone.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SupportNotifyTest {
    @Test
    fun notifiesOncePerNewReply() {
        assertTrue(SupportNotify.shouldNotify(unread = 1, lastId = 12, notifiedId = 0))
        assertFalse(SupportNotify.shouldNotify(unread = 1, lastId = 12, notifiedId = 12))   // כבר התרענו
        assertTrue(SupportNotify.shouldNotify(unread = 2, lastId = 15, notifiedId = 12))    // תשובה נוספת
        assertFalse(SupportNotify.shouldNotify(unread = 0, lastId = 15, notifiedId = 12))   // כבר נקרא
    }
}
