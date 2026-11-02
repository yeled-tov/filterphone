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
     * אחת. אם אין אפליקציות חשבון — סתם מריצים את ההקמה. מה שהופך אותו לאמין:
     *   - משביתים רק מה שדלוק עכשיו (ומחזירים רק אותו) — אפליקציה שהמשתמש כיבה בעצמו נשארת כבויה.
     *   - אחרי ההשבתה מחכים כמה שניות: אנדרואיד מעדכן את רשימת "מנהלי החשבונות" ברקע, ובלי ההמתנה
     *     ה-dpm עוד רואה את החשבונות ונכשל. ואם עדיין "יש חשבונות" — עוד ניסיון או שניים.
     *   - trap: גם אם החיבור שלנו נופל באמצע (האפליקציה נסגרה), המעטפת לא נעצרת — ממשיכה עד ההחזרה.
     *   - מדווח מה הושבת (FP_DISABLED=…) ומה לא הצליח להשבית (FP_DISABLE_FAIL=…) — כדי להסביר מה חוסם.
     * החשבונות עצמם לא נמחקים: חשבון נמחק רק כשאפליקציית החשבון *הוסרה* — השבתה רק מסתירה אותו לרגע.
     */
    fun accountTrick(pkg: String, accountPkgs: List<String>): String {
        if (accountPkgs.isEmpty()) return setDeviceOwner(pkg)
        val list = accountPkgs.joinToString(" ")
        return listOf(
            "trap '' HUP PIPE INT TERM 2>/dev/null",
            "E=\$(pm list packages -e 2>/dev/null)",
            "D=''",
            "for p in $list; do " +
                "if echo \"\$E\" | grep -qx \"package:\$p\"; then " +
                "r=\$(pm disable-user --user 0 \$p 2>&1); " +
                "case \"\$r\" in *disabled-user*) D=\"\$D \$p\";; *) echo \"FP_DISABLE_FAIL=\$p \$r\" | head -c 300; echo;; esac; " +
                "fi; done",
            "echo \"FP_DISABLED=\$D\"",
            "sleep $TRICK_SETTLE_S",
            "i=0; rc=1",
            "while [ \$i -lt $TRICK_TRIES ]; do " +
                "o=\$(dpm set-device-owner ${admin(pkg)} 2>&1); rc=\$?; echo \"\$o\"; " +
                "case \"\$o\" in *uccess*) rc=0; break;; *ccounts*) ;; *) break;; esac; " +
                "i=\$((i+1)); sleep 2; done",
            "for p in \$D; do pm enable \$p >/dev/null 2>&1; done",
            "echo FP_RC=\$rc",
        ).joinToString("; ")
    }

    /** כמה שניות לחכות אחרי ההשבתה, וכמה ניסיונות dpm כשעדיין "יש חשבונות". */
    private const val TRICK_SETTLE_S = 3
    private const val TRICK_TRIES = 3

    /** מה הטריק השבית (כדי להדליק שוב לביטחון) — מהשורה FP_DISABLED=… */
    fun disabledBy(output: String): List<String> =
        output.lineSequence().firstOrNull { it.trim().startsWith("FP_DISABLED=") }
            ?.substringAfter("FP_DISABLED=")?.trim()?.split(Regex("\\s+"))?.filter { it.isNotBlank() }.orEmpty()

    /** מה הטריק לא הצליח להשבית (המערכת סירבה) — מהשורות FP_DISABLE_FAIL=… */
    fun disableFailures(output: String): List<String> =
        output.lineSequence().map { it.trim() }.filter { it.startsWith("FP_DISABLE_FAIL=") }
            .map { it.substringAfter("FP_DISABLE_FAIL=").substringBefore(' ').trim() }
            .filter { it.isNotBlank() }.distinct().toList()

    /** הדלקה חוזרת (בטוחה לחזור עליה) של מה שהושבת — רשת ביטחון אחרי הטריק. */
    fun reenable(pkgs: List<String>): String? =
        if (pkgs.isEmpty()) null else "for p in ${pkgs.joinToString(" ")}; do pm enable \$p >/dev/null 2>&1; done"

    /**
     * אפליקציות החשבון שבאמת יש להן חשבון אצל המשתמש הראשי — מתוך `dumpsys account` (סוגי החשבונות +
     * "ServiceInfo: AuthenticatorDescription {type=…}, ComponentInfo{package/…}"). כך משביתים לרגע רק את
     * מה שחוסם (למשל שירותי Google + חשבון Xiaomi + וואטסאפ), לא כל אפליקציה שיכולה להחזיק חשבון. טהור.
     */
    fun authenticatorsWithAccounts(dumpsys: String, self: String): List<String> {
        val types = accountTypes(dumpsys).toSet()
        if (types.isEmpty()) return emptyList()
        val owners = LinkedHashSet<String>()
        var inOwner = true
        val svc = Regex("""AuthenticatorDescription \{type=([A-Za-z0-9_.\-]+)\}.*?ComponentInfo\{([A-Za-z0-9_.]+)/""")
        for (line in dumpsys.lineSequence()) {
            val t = line.trim()
            if (t.startsWith("User UserInfo{")) inOwner = t.startsWith("User UserInfo{0:")
            if (!inOwner) continue
            val m = svc.find(t) ?: continue
            if (m.groupValues[1] in types) owners += m.groupValues[2]
        }
        return accountPackages(owners.toList(), self)
    }

    /** סוגי החשבונות שהאפליקציות האלה מחזיקות — כדי להסביר בדיוק מה לא הושבת. טהור. */
    fun typesOf(dumpsys: String, pkgs: Collection<String>): List<String> {
        if (pkgs.isEmpty()) return emptyList()
        val types = accountTypes(dumpsys).toSet()
        val svc = Regex("""AuthenticatorDescription \{type=([A-Za-z0-9_.\-]+)\}.*?ComponentInfo\{([A-Za-z0-9_.]+)/""")
        return dumpsys.lineSequence().mapNotNull { svc.find(it.trim()) }
            .filter { it.groupValues[2] in pkgs && it.groupValues[1] in types }
            .map { it.groupValues[1] }.distinct().toList()
    }

    /**
     * הרשאות והגדרות אחרי שהפכנו למנהל מכשיר (או כשרוצים גם בלי): WRITE_SECURE_SETTINGS (המגן נדלק
     * לבד), פטור סוללה ועבודה ברקע, אישור VPN (רק כשלא מנהל מכשיר), והרשאת התראות.
     */
    fun followups(pkg: String, sdk: Int, owner: Boolean, xiaomi: Boolean = false): List<String> {
        val cmds = ArrayList<String>()
        cmds += "pm grant $pkg android.permission.WRITE_SECURE_SETTINGS 2>&1"
        cmds += "dumpsys deviceidle whitelist +$pkg 2>&1"
        cmds += "cmd appops set $pkg RUN_IN_BACKGROUND allow 2>&1"
        cmds += "cmd appops set $pkg RUN_ANY_IN_BACKGROUND allow 2>&1"
        if (!owner) cmds += "appops set $pkg ACTIVATE_VPN allow 2>&1"
        if (sdk >= 33) cmds += "pm grant $pkg android.permission.POST_NOTIFICATIONS 2>&1"
        if (xiaomi) cmds += xiaomiGrants(pkg)
        return cmds
    }

    /**
     * שיאומי / רדמי / פוקו: ההרשאות של MIUI עצמה (מעבר לאנדרואיד), שבלעדיהן המערכת עוצרת את המגן ברקע
     * ולא נותנת לו לפתוח חלונות (בחירת קישור, דיווח, בקשת אישור): "הפעלה אוטומטית" (10008), "הצגת חלונות
     * קופצים ברקע" (10021) ו"הצגה על מסך הנעילה" (10020). עובד כשההקמה רצה עם "ניפוי באגים (הגדרות
     * אבטחה)"; בגרסה שלא מכירה — הפקודה פשוט נכשלת בלי נזק.
     */
    fun xiaomiGrants(pkg: String): List<String> =
        listOf(10008, 10021, 10020).map { "appops set $pkg $it allow 2>&1" }

    /**
     * שיאומי (MIUI / HyperOS) עם **root בלבד**: פותח את "ניפוי באגים ב-USB (הגדרות אבטחה)" דרך מאפייני
     * המערכת, כדי שאפשר יהיה לצרוב מנהל מכשיר **בלי חשבון Mi ובלי SIM**. המאפיינים האלה מוגנים
     * (`persist.security.*`) — רק root יכול לכתוב אותם; המעטפת של ADB (ניפוי אלחוטי) לא. לכן זה עוזר רק
     * במכשיר מושרש. בגרסה שלא מכירה אותם — הפקודה פשוט לא משנה כלום (בלי נזק). טהור.
     */
    fun xiaomiSecurityProps(): String =
        "setprop persist.security.adbinput 1 2>&1; setprop persist.security.adbinstall 1 2>&1"

    /** האם הכישלון הוא בגלל חשבונות במכשיר (ואז מנסים את הטריק). */
    fun blockedByAccounts(output: String): Boolean =
        RootProvisioner.classifyDpmFailure(output) == "accounts_present"

    /**
     * סוגי החשבונות שבמכשיר (של המשתמש הראשי), מתוך הפלט של `dumpsys account` — רק הסוג
     * (com.google, com.osp.app.signin…), לא השם/המייל. כדי להסביר למשתמש איזה חשבון חוסם את ההקמה.
     */
    fun accountTypes(dumpsys: String): List<String> {
        val out = LinkedHashSet<String>()
        var inOwner = true
        for (line in dumpsys.lineSequence()) {
            val t = line.trim()
            if (t.startsWith("User UserInfo{")) inOwner = t.startsWith("User UserInfo{0:")
            if (!inOwner || !t.startsWith("Account {")) continue
            Regex("""type=([A-Za-z0-9_.\-]+)""").find(t)?.let { out += it.groupValues[1] }
        }
        return out.toList()
    }

    /** משתמשים נוספים במכשיר (תיקייה מאובטחת, אפליקציות כפולות, מרחב שני) מתוך `pm list users`. */
    fun extraUsers(pmListUsers: String): List<String> =
        Regex("""UserInfo\{(\d+):([^:}]*)""").findAll(pmListUsers)
            .filter { it.groupValues[1] != "0" }
            .map { it.groupValues[2].trim().ifEmpty { "משתמש ${it.groupValues[1]}" } }
            .distinct().toList()

    /** האם ה-set-device-owner הצליח לפי הפלט (אין צורך להסתמך רק על בדיקת המערכת). */
    fun succeeded(output: String): Boolean {
        val o = output.lowercase()
        return ("success" in o && "device owner" in o) || "fp_rc=0" in o
    }
}
