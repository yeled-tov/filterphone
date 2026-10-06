package com.filterphone.app

import java.text.Normalizer
import java.util.Locale

/**
 * חסימה לפי מילות חיפוש: משווה טקסט שמופיע על המסך (בעיקר מה שהמשתמש מקליד בשדה חיפוש) מול
 * רשימת חסימה שמגיעה מהשרת. אם נמצאה התאמה — המגן מחזיר את המשתמש אחורה (GuardService).
 *
 * פרטיות: המחלקה מקבלת טקסט, משווה אותו לרשימה, ומחזירה רק את המילה שהתאימה (או null). היא לא
 * שומרת, לא מעתיקה ולא שולחת את הטקסט לשום מקום. GuardService מדלג על שדות סיסמה.
 *
 * נרמול (normalize): אותיות קטנות, הסרת ניקוד/טעמים (NFKD + הסרת סימני צירוף), וכל תו שאינו
 * אות/ספרה הופך לרווח יחיד. כך "סֶקְס", "S E X" ו-"porn!!!" מזוהים.
 *
 * התאמה:
 *   - מילה באנגלית בלבד (a–z/0–9, בלי רווח) — התאמת מילה שלמה, כדי לא לחסום בטעות (sex ≠ Sussex).
 *   - כל השאר (עברית, ערבית, רוסית, סינית, ביטוי עם רווח) — התאמת תת-מחרוזת, כדי לתפוס גם צורות
 *     עם קידומת/הטיה ("בביקיני" מכיל "ביקיני") ושפות בלי רווחים בין מילים.
 *
 * בלי תלות באנדרואיד — נבדקת בבדיקות יחידה.
 */
class KeywordDetector(terms: List<String>) {
    private val wordTerms: Set<String>
    private val substringTerms: List<String>

    init {
        val words = HashSet<String>()
        val subs = LinkedHashSet<String>()
        for (raw in terms) {
            val n = normalize(raw)
            if (n.isEmpty()) continue
            if (n.none { it == ' ' } && n.all { it in 'a'..'z' || it in '0'..'9' }) words += n else subs += n
        }
        wordTerms = words
        substringTerms = subs.toList()
    }

    val isEmpty: Boolean get() = wordTerms.isEmpty() && substringTerms.isEmpty()

    /** המילה החסומה הראשונה שנמצאה בטקסט, או null. */
    fun match(text: CharSequence?): String? {
        val norm = normalize(text)
        if (norm.isEmpty() || isEmpty) return null
        val hay = if (norm.length > MAX_LEN) norm.substring(0, MAX_LEN) else norm
        for (term in substringTerms) if (hay.contains(term)) return term
        if (wordTerms.isNotEmpty()) {
            var i = 0
            while (i < hay.length) {
                var j = hay.indexOf(' ', i)
                if (j < 0) j = hay.length
                if (j > i && hay.substring(i, j) in wordTerms) return hay.substring(i, j)
                i = j + 1
            }
        }
        return null
    }

    companion object {
        const val MAX_LEN = 2000

        fun normalize(s: CharSequence?): String {
            if (s.isNullOrEmpty()) return ""
            val lowered = s.toString().lowercase(Locale.ROOT)
            val decomposed = Normalizer.normalize(lowered, Normalizer.Form.NFKD)
            val sb = StringBuilder(decomposed.length)
            var prevSpace = true
            for (ch in decomposed) {
                if (Character.getType(ch) == Character.NON_SPACING_MARK.toInt()) continue   // ניקוד / טעמים
                if (Character.isLetterOrDigit(ch)) {
                    sb.append(ch)
                    prevSpace = false
                } else if (!prevSpace) {
                    sb.append(' ')
                    prevSpace = true
                }
            }
            return sb.toString().trim()
        }
    }
}
