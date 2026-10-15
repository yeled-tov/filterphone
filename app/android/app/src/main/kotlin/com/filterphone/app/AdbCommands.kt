package com.filterphone.app

/**
 * בניית פקודות ההקמה שרצות דרך ADB (ניפוי אלחוטי) או root — טהור, נבדק ביחידה.
 *
 * הטריק של סוויץ' לחשבונות: אנדרואיד חוסם הפיכה למנהל מכשיר כשיש חשבונות במכשיר. לכן כשההקמה
 * הרגילה נכשלת בגלל חשבונות — משביתים *זמנית* את אפליקציות החשבון (למשל שירותי Google),
 * מריצים את ההקמה, ומדליקים אותן מיד בחזרה — הכול בפקודה אחת, כך שגם אם התהליך שלנו ייפול
 * באמצע, adbd מסיים את כל הרצף (והחשבונות חוזרים). בלי איפוס ובלי מחיקת חשבונות.
 */
object AdbCommands {
    const val ADMIN_SUFFIX = "/.DeviceAdmin"

    /** שם רכיב מנהל המכשיר (package/.DeviceAdmin). */
    fun admin(pkg: String) = "$pkg$ADMIN_SUFFIX"

    /** ההקמה הישירה: הופכים את FilterPhone למנהל מכשיר. (stderr מופנה כדי שנראה את הסיבה.) */
    fun setDeviceOwner(pkg: String) = "dpm set-device-owner ${admin(pkg)} 2>&1"

    /** חבילות מערכת ש*לא* משביתים גם בטריק (מסגרת אנדרואיד). */
    private val NEVER_DISABLE = setOf("android", "com.android.systemui")

    /**
     * אפליקציות החשבון שאפשר להשבית זמנית כדי לעקוף את חסימת החשבונות: כל מי שרשום כ"מנהל חשבונות"
     * (authenticator) בטלפון, חוץ מ-FilterPhone ומרכיבי המסגרת. בלי הרשאת GET_ACCOUNTS — נגזר מרשימת
     * ה-authenticators (getAuthenticatorTypes), שלא דורשת הרשאה. השבתה של מי שאין לו חשבונות — לא מזיקה
     * (מדליקים בחזרה מיד).
     */
    fun accountPackages(authenticatorPkgs: List<String>, self: String): List<String> =
        authenticatorPkgs.filter { it.isNotBlank() && it != self && it !in NEVER_DISABLE }
            .distinct()
            .sorted()

    /**
     * הטריק: משביתים זמנית את אפליקציות החשבון, מריצים set-device-owner, ומדליקים בחזרה — בפקודה
     * אחת אטומית. אם אין אפליקציות חשבון — סתם מריצים את ההקמה.
     */
    fun accountTrick(pkg: String, accountPkgs: List<String>): String {
        if (accountPkgs.isEmpty()) return setDeviceOwner(pkg)
        val list = accountPkgs.joinToString(" ")
        return buildString {
            append("for p in $list; do pm disable-user --user 0 \$p 2>/dev/null; done; ")
            append("dpm set-device-owner ${admin(pkg)} 2>&1; rc=\$?; ")
            append("for p in $list; do pm enable \$p 2>/dev/null; done; ")
            append("echo FP_RC=\$rc")
        }
    }

    /**
     * הרשאות והגדרות אחרי שהפכנו למנהל מכשיר (או כשרוצים גם בלי): WRITE_SECURE_SETTINGS (המגן נדלק
     * לבד), פטור סוללה ועבודה ברקע, אישור VPN (רק כשלא מנהל מכשיר), והרשאת התראות.
     */
    fun followups(pkg: String, sdk: Int, owner: Boolean): List<String> {
        val cmds = ArrayList<String>()
        cmds += "pm grant $pkg android.permission.WRITE_SECURE_SETTINGS 2>&1"
        cmds += "dumpsys deviceidle whitelist +$pkg 2>&1"
        cmds += "cmd appops set $pkg RUN_IN_BACKGROUND allow 2>&1"
        cmds += "cmd appops set $pkg RUN_ANY_IN_BACKGROUND allow 2>&1"
        if (!owner) cmds += "appops set $pkg ACTIVATE_VPN allow 2>&1"
        if (sdk >= 33) cmds += "pm grant $pkg android.permission.POST_NOTIFICATIONS 2>&1"
        return cmds
    }

    /** האם הכישלון הוא בגלל חשבונות במכשיר (ואז מנסים את הטריק). */
    fun blockedByAccounts(output: String): Boolean =
        RootProvisioner.classifyDpmFailure(output) == "accounts_present"

    /** האם ה-set-device-owner הצליח לפי הפלט (אין צורך להסתמך רק על בדיקת המערכת). */
    fun succeeded(output: String): Boolean {
        val o = output.lowercase()
        return ("success" in o && "device owner" in o) || "fp_rc=0" in o
    }
}
