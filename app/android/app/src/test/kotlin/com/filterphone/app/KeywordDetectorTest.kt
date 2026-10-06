package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class KeywordDetectorTest {
    private val det = KeywordDetector(listOf("sex", "porn", "bikini", "סקס", "ביקיני", "פורנו", "секс", "色情", "bad word"))

    @Test
    fun matchesEnglishWholeWordInAnyPosition() {
        assertEquals("sex", det.match("how to sex"))
        assertEquals("sex", det.match("SEX"))
        assertEquals("porn", det.match("free porn videos"))
        assertEquals("bikini", det.match("red BIKINI photos"))
    }

    @Test
    fun englishIsWholeWordSoItDoesNotCatchInnocentSubstrings() {
        assertNull(det.match("university of sussex"))
        assertNull(det.match("essex county"))
        assertNull(det.match("i love my sexton")) // "sexton" ≠ "sex"
    }

    @Test
    fun hebrewAndOtherScriptsMatchAsSubstringToCatchPrefixes() {
        assertEquals("סקס", det.match("סקס"))
        assertEquals("ביקיני", det.match("בביקיני"))      // קידומת ב'
        assertEquals("פורנו", det.match("אתרי פורנוגרפיה")) // הטיה
        assertEquals("секс", det.match("порно секс"))
        assertEquals("色情", det.match("查找色情内容"))         // סינית — בלי רווחים
    }

    @Test
    fun stripsNiqqudAndSurroundingPunctuation() {
        assertEquals("סקס", det.match("סֶקְס"))          // ניקוד מוסר
        assertEquals("porn", det.match("porn!!!"))        // סימני פיסוק → רווח/קצה
        assertEquals("porn", det.match("(porn)"))
        assertEquals("sex", det.match("...sex..."))
    }

    @Test
    fun multiWordPhraseMatchesAsSubstring() {
        assertEquals("bad word", det.match("this is a Bad   Word here"))
    }

    @Test
    fun cleanTextIsNotBlocked() {
        assertNull(det.match("שלום עולם"))
        assertNull(det.match("hello world"))
        assertNull(det.match("waze navigation home"))
        assertNull(det.match(""))
        assertNull(det.match(null))
    }

    @Test
    fun emptyDetectorMatchesNothing() {
        val empty = KeywordDetector(emptyList())
        assertTrue(empty.isEmpty)
        assertNull(empty.match("sex porn"))
    }

    @Test
    fun longTextIsCappedButStillScansTheStart() {
        val filler = "a ".repeat(3000)
        assertEquals("sex", det.match("sex $filler"))      // ההתאמה בהתחלה נתפסת
    }
}
