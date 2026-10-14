package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Test

class AccessibilityGuardTest {
    private val me = "com.filterphone.app/com.filterphone.app.GuardService"

    @Test
    fun addsOurServiceWithoutTouchingOthers() {
        assertEquals(me, AccessibilityGuard.merge("", me))
        assertEquals("com.other/.Svc:$me", AccessibilityGuard.merge("com.other/.Svc", me))
    }

    @Test
    fun doesNotDuplicateInEitherForm() {
        assertEquals(me, AccessibilityGuard.merge(me, me))
        val short = "com.filterphone.app/.GuardService"
        assertEquals("com.other/.Svc:$short", AccessibilityGuard.merge("com.other/.Svc:$short", me))
    }
}
