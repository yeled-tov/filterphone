package com.filterphone.app

import android.content.Context
import android.content.pm.ApplicationInfo
import android.os.Build
import android.util.Log

/**
 * Google Play פתוח ומנוהל (האופציה play_managed): הלקוח נכנס לחנות, גולש ומחפש כרגיל — אבל מתקין רק את
 * מה שהמנהל אישר (allowlist במדיניות). שתי שכבות:
 *   1. לפני ההתקנה (נגישות): בעמוד של אפליקציה שאינה מאושרת — כפתור "התקנה" מכוסה ב"חסום להתקנה —
 *      בקשת אישור" (PlayRequestBadges), שפותח את חלון הבקשה (AppRequestActivity).
 *   2. אחרי ההתקנה (הבטחה): אפליקציה חדשה מ-Play שאינה מאושרת — מוסרת מיד בשקט (מנהל מכשיר), או נחסמת
 *      לפתיחה (בלי מנהל מכשיר), וחלון הבקשה נפתח עם השם המדויק שלה.
 * כאן ההחלטות (טהור, נבדק ביחידה) והרשימה המקומית של התקנות שנחסמו.
 */
object PlayManaged {
    private const val TAG = "FilterPhone"
    private const val PREFS = "fp_play_managed"
    private const val K_BLOCKED = "blocked_installs"
    const val PLAY = "com.android.vending"

    /** הכפתור הוא כפתור התקנה (כולל מחיר של אפליקציה בתשלום — "₪4.90"). טהור. */
    fun isInstallButton(text: String?): Boolean {
        val t = norm(text ?: return false)
        if (t.isEmpty() || t.length > 20) return false
        return t in INSTALL || PRICE.containsMatchIn(t)
    }

    /** העמוד הוא של אפליקציה מאושרת (השם שלה בראש המסך). טהור. */
    fun pageApproved(texts: List<String>, approvedTitles: List<String>): Boolean =
        approvedTitles.isNotEmpty() && PlayLock.isTargetPage(texts, approvedTitles)

    /** שם האפליקציה בעמוד (לבקשה — הלקוח יכול לתקן): הטקסט הראשון שנראה כמו שם. טהור. */
    fun guessTitle(texts: List<String>): String =
        texts.asSequence().map { it.trim() }
            .filter { it.length in 2..60 && norm(it) !in NOT_TITLES && !isInstallButton(it) && !RATING.containsMatchIn(it) }
            .take(PlayLock.HEAD_TEXTS).firstOrNull().orEmpty()

    /** אפליקציה חדשה מ-Play שצריך לחסום: לא מאושרת, הותקנה עכשיו (לא עדכון), מ-Play, ולא אפליקציית מערכת. טהור. */
    fun shouldBlockInstall(pkg: String, approved: Set<String>, replacing: Boolean, installer: String?,
                           system: Boolean, protected: Set<String>): Boolean =
        !replacing && installer == PLAY && !system && pkg !in approved && pkg !in protected

    // ------------------------------------------------------------ התקנה חדשה (PackageWatch)

    /** התקנה חדשה: לא מאושרת מ-Play → הסרה שקטה (מנהל מכשיר) או חסימה לפתיחה, וחלון הבקשה. */
    fun onPackageAdded(context: Context, pkg: String, replacing: Boolean) {
        val app = context.applicationContext
        val policy = GuardPolicy.get(app)
        if (!policy.playManaged || AdminState.isPaused(app)) return
        val pm = app.packageManager
        val info = runCatching { pm.getApplicationInfo(pkg, 0) }.getOrNull() ?: return
        val installer = runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) pm.getInstallSourceInfo(pkg).installingPackageName
            else @Suppress("DEPRECATION") pm.getInstallerPackageName(pkg)
        }.getOrNull()
        val system = (info.flags and ApplicationInfo.FLAG_SYSTEM) != 0
        if (!shouldBlockInstall(pkg, policy.allowed, replacing, installer, system, GuardPolicy.untouchable(app))) return
        val label = runCatching { pm.getApplicationLabel(info).toString() }.getOrDefault(pkg)
        Log.i(TAG, "play managed: $pkg is not approved — ${if (DeviceOwnerManager.isDeviceOwner(app)) "removing" else "blocking"}")
        if (DeviceOwnerManager.isDeviceOwner(app)) {
            ManagedApps.uninstall(app, pkg)
        } else {
            markBlocked(app, pkg)
        }
        AppRequestActivity.open(app, label, pkg, removed = true)
    }

    /** בלי מנהל מכשיר: התקנות לא מאושרות נחסמות לפתיחה (עד שאושרו). */
    fun isBlockedInstall(context: Context, pkg: String): Boolean =
        pkg in prefs(context).getStringSet(K_BLOCKED, emptySet()).orEmpty()

    private fun markBlocked(context: Context, pkg: String) {
        val cur = prefs(context).getStringSet(K_BLOCKED, emptySet()).orEmpty()
        prefs(context).edit().putStringSet(K_BLOCKED, (cur + pkg).toSet()).apply()
    }

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun openRequest(context: Context, title: String) {
        runCatching { AppRequestActivity.open(context, title, null, removed = false) }
            .onFailure { Log.w(TAG, "request window failed", it) }
    }

    private val INSTALL = setOf("התקנה", "התקן", "install", "get", "קבל")
    private val PRICE = Regex("^(₪|\\$|€|£|ils)\\s?\\d|\\d[.,]?\\d*\\s?(₪|\\$|€|£|ils)$")
    private val RATING = Regex("^\\d([.,]\\d)?\\s?★|^\\d+([.,]\\d+)?\\s?(k|m|אלף|מיליון)?\\+?$")
    private val NOT_TITLES = setOf("ניווט למעלה", "חזרה", "navigate up", "back", "חיפוש", "search", "עוד אפשרויות",
        "more options", "התקנה", "פתיחה", "עדכון", "הסרת התקנה", "install", "open", "update", "uninstall",
        "מכיל מודעות", "contains ads", "רכישות מתוך האפליקציה", "in-app purchases")

    private fun norm(s: String): String = s.replace(Regex("[\\u200e\\u200f\\u202a-\\u202e]"), "")
        .replace(Regex("\\s+"), " ").trim().lowercase()
}
