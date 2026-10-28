package com.filterphone.app

/**
 * מסך "הוספת חשבון Google" (בהגדרות, ומסך הכניסה של שירותי Google). כשהוא נפתח — מבקשים מהשרת את
 * המדיניות מיד: אם המנהל הדליק "הוספת חשבונות Google", שירותי Google יוצאים מה-VPN (רק הם) ומסך
 * הכניסה עובד — בלי לחכות לדיווח היומי. טהור — נבדק ביחידה.
 */
object AccountScreens {
    private val PACKAGES = setOf("com.android.settings", "com.google.android.gms")
    private val CLASS = Regex("(AddAccount|ChooseAccount|MinuteMaid|AccountIntro|auth\\.uiflows|auth\\.login)",
        RegexOption.IGNORE_CASE)

    fun isAddAccount(pkg: String?, cls: String?): Boolean =
        pkg != null && cls != null && (pkg in PACKAGES || pkg.endsWith(".settings")) && CLASS.containsMatchIn(cls)
}
