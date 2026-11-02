package com.filterphone.app

import android.app.AppOpsManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Process
import android.util.Log

/**
 * שיאומי / רדמי / פוקו (MIUI / HyperOS): מעבר לאופטימיזציית הסוללה של אנדרואיד יש להם שתי הגבלות משלהם,
 * שבלעדיהן המערכת עוצרת את מגן FilterPhone (הנגישות) ברקע — גם כשאנדרואיד כבר אומר "לא מוגבל":
 *   1. "הפעלה אוטומטית" (Autostart) — ב"אבטחה" ← הרשאות.
 *   2. "חיסכון בסוללה" של האפליקציה ← "ללא הגבלות".
 * כאן: זיהוי, פתיחת המסכים האלה ישירות (עם חלופות), ובדיקת "הפעלה אוטומטית" כשאפשר.
 */
object Xiaomi {
    private const val TAG = "FilterPhone"
    /** הרשאת "הפעלה אוטומטית" של MIUI (AppOps פנימי — לא חלק מאנדרואיד הרגיל). */
    private const val OP_AUTO_START = 10008

    /** טהור — לפי היצרן / המותג. */
    fun isXiaomi(manufacturer: String, brand: String): Boolean =
        SetupGuide.vendor(manufacturer, brand) == "xiaomi"

    val isDevice: Boolean get() = isXiaomi(Build.MANUFACTURER.orEmpty(), Build.BRAND.orEmpty())

    /** "הצגת חלונות קופצים בזמן ריצה ברקע" — בלעדיה MIUI לא נותנת למגן לפתוח חלון (בחירת קישור, דיווח). */
    private const val OP_BACKGROUND_START_ACTIVITY = 10021

    /** "הפעלה אוטומטית" מופעלת? null = לא ידוע (גרסה שלא חושפת). */
    fun autostartAllowed(context: Context): Boolean? = opAllowed(context, OP_AUTO_START)

    /** "חלונות קופצים ברקע" מותרים? null = לא ידוע. */
    fun popupsAllowed(context: Context): Boolean? = opAllowed(context, OP_BACKGROUND_START_ACTIVITY)

    private fun opAllowed(context: Context, op: Int): Boolean? {
        if (!isDevice) return null
        return runCatching {
            val ops = context.getSystemService(AppOpsManager::class.java)
            val m = AppOpsManager::class.java.getMethod("checkOpNoThrow", Int::class.javaPrimitiveType,
                Int::class.javaPrimitiveType, String::class.java)
            (m.invoke(ops, op, Process.myUid(), context.packageName) as Int) == AppOpsManager.MODE_ALLOWED
        }.getOrNull()
    }

    /** "הרשאות נוספות" של FilterPhone (שם: "הצגת חלונות קופצים בזמן ריצה ברקע", "הצגה במסך הנעילה"). */
    fun permissionIntents(context: Context): List<Intent> = listOf(
        Intent("miui.intent.action.APP_PERM_EDITOR").setClassName("com.miui.securitycenter",
            "com.miui.permcenter.permissions.PermissionsEditorActivity").putExtra("extra_pkgname", context.packageName),
        Intent("miui.intent.action.APP_PERM_EDITOR").setClassName("com.miui.securitycenter",
            "com.miui.permcenter.permissions.AppPermissionsEditorActivity").putExtra("extra_pkgname", context.packageName),
        Intent("miui.intent.action.APP_PERM_EDITOR").putExtra("extra_pkgname", context.packageName),
    )

    /** מסך "הפעלה אוטומטית" (רשימת האפליקציות). */
    fun autostartIntents(): List<Intent> = listOf(
        Intent().setComponent(ComponentName("com.miui.securitycenter",
            "com.miui.permcenter.autostart.AutoStartManagementActivity")),
        Intent("miui.intent.action.OP_AUTO_START").addCategory(Intent.CATEGORY_DEFAULT),
    )

    /** "חיסכון בסוללה" של FilterPhone (כדי לבחור "ללא הגבלות"). */
    fun batteryIntents(context: Context): List<Intent> = listOf(
        Intent("miui.intent.action.HIDDEN_APPS_CONFIG_ACTIVITY")
            .putExtra("package_name", context.packageName)
            .putExtra("package_label", context.applicationInfo.loadLabel(context.packageManager).toString()),
        Intent().setComponent(ComponentName("com.miui.powerkeeper",
            "com.miui.powerkeeper.ui.HiddenAppsConfigActivity"))
            .putExtra("package_name", context.packageName)
            .putExtra("package_label", context.applicationInfo.loadLabel(context.packageManager).toString()),
    )

    /** הראשון מהרשימה שקיים בטלפון — או null. */
    fun firstAvailable(context: Context, intents: List<Intent>): Intent? =
        intents.firstOrNull { runCatching { it.resolveActivity(context.packageManager) != null }.getOrDefault(false) }
            ?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

    /** פותח את המסך הראשון שקיים. false — אף אחד לא נפתח. */
    fun open(context: Context, intents: List<Intent>): Boolean {
        val i = firstAvailable(context, intents) ?: return false
        return runCatching { context.startActivity(i); true }
            .onFailure { Log.w(TAG, "xiaomi screen failed: ${it.javaClass.simpleName}") }.getOrDefault(false)
    }

    /** מה עוד צריך לאשר בשיאומי (לדיווח ולמסך): autostart / battery. */
    fun report(context: Context): Map<String, Any?> =
        if (!isDevice) emptyMap() else mapOf("xiaomi" to true, "autostart" to autostartAllowed(context),
            "popups" to popupsAllowed(context))
}
