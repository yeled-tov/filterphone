package com.filterphone.app

import android.content.SharedPreferences
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** השהיה הולכת וגדלה: בלי לולאת ניסיונות שמרוקנת סוללה, ואיפוס בהצלחה. */
class BackoffTest {
    private class MemPrefs : SharedPreferences {
        val m = HashMap<String, Any?>()
        override fun getAll(): MutableMap<String, *> = m
        override fun getString(k: String?, d: String?) = m[k] as String? ?: d
        override fun getStringSet(k: String?, d: MutableSet<String>?) = d
        override fun getInt(k: String?, d: Int) = m[k] as Int? ?: d
        override fun getLong(k: String?, d: Long) = m[k] as Long? ?: d
        override fun getFloat(k: String?, d: Float) = d
        override fun getBoolean(k: String?, d: Boolean) = m[k] as Boolean? ?: d
        override fun contains(k: String?) = m.containsKey(k)
        override fun registerOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener?) {}
        override fun unregisterOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener?) {}
        override fun edit(): SharedPreferences.Editor = object : SharedPreferences.Editor {
            val pending = HashMap<String, Any?>()
            val removed = HashSet<String>()
            override fun putString(k: String, v: String?) = apply { pending[k] = v }
            override fun putStringSet(k: String, v: MutableSet<String>?) = apply { pending[k] = v }
            override fun putInt(k: String, v: Int) = apply { pending[k] = v }
            override fun putLong(k: String, v: Long) = apply { pending[k] = v }
            override fun putFloat(k: String, v: Float) = apply { pending[k] = v }
            override fun putBoolean(k: String, v: Boolean) = apply { pending[k] = v }
            override fun remove(k: String) = apply { removed += k }
            override fun clear() = apply { m.clear() }
            override fun commit(): Boolean { apply(); return true }
            override fun apply() { removed.forEach { m.remove(it) }; m.putAll(pending) }
        }
    }

    @Test
    fun delaysGrowThenCapAndResetOnSuccess() {
        var now = 1_000_000L
        val b = Backoff(MemPrefs(), "vpn", clock = { now })
        assertTrue(b.ready())
        val delays = (1..10).map { b.failed() }
        assertEquals(listOf(5_000L, 15_000L, 30_000L, 60_000L, 120_000L, 300_000L, 600_000L, 900_000L, 900_000L, 900_000L), delays)
        assertFalse(b.ready())
        now += 899_999
        assertFalse(b.ready())
        now += 1
        assertTrue(b.ready())
        b.succeeded()
        assertEquals(0, b.attempts)
        assertEquals(5_000L, b.failed())
    }

    @Test
    fun lockdownCapsTheWaitEvenWhenALongOneWasSaved() {
        // בלי מנהרה + lockdown = בלי אינטרנט: המתנה של 15 דקות (שנשמרה מלפני אתחול) מתקצרת ל-30 שניות
        var now = 0L
        val b = Backoff(MemPrefs(), "vpn", clock = { now })
        repeat(9) { b.failed() }
        assertEquals(900_000L, b.waitMs())
        b.capWait(30_000L)
        assertEquals(30_000L, b.waitMs())
        b.capWait(60_000L)                     // לא מאריך
        assertEquals(30_000L, b.waitMs())
        assertEquals(30_000L, b.failed(30_000L))
        now += 30_000L
        assertTrue(b.ready())
    }

    @Test
    fun stateSurvivesProcessDeath() {
        val prefs = MemPrefs()
        var now = 0L
        Backoff(prefs, "vpn", clock = { now }).apply { failed(); failed() }
        val again = Backoff(prefs, "vpn", clock = { now })      // תהליך חדש, אותו אחסון
        assertEquals(2, again.attempts)
        assertFalse(again.ready())
        now += 15_000
        assertTrue(again.ready())
    }
}
