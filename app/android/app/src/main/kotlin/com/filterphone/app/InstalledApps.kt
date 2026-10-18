package com.filterphone.app

import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageInstaller
import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Base64
import java.io.ByteArrayOutputStream
import java.io.File

/**
 * טאב "עדכונים" בחנות: כל האפליקציות שבטלפון (שם, גרסה, מתי עודכנה, אייקון), ופס ההתקנה.
 * רץ רק כשפותחים את הטאב — לא ברקע (חיסכון בסוללה).
 */
object InstalledApps {
    private const val ICON_PX = 96
    private const val PLAY = "com.android.vending"

    /**
     * אפליקציות עם אייקון במסך הבית — חוץ מ-FilterPhone, המקלדת ומסך הבית, ואפליקציות מערכת שלא
     * עודכנו מעולם (הן מגיעות עם הטלפון ולא מ-Play). ממוין לפי שם. לכל אחת גם: האם היא אפליקציית
     * מערכת (שעודכנה), האם הותקנה מ-Google Play, וגודל קובצי ההתקנה — לתגיות ולשורת הגודל בכרטיס.
     */
    fun forUpdates(context: Context): List<Map<String, Any?>> {
        val pm = context.packageManager
        val skip = GuardPolicy.untouchable(context) + context.packageName
        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val pkgs = pm.queryIntentActivities(launcher, 0).map { it.activityInfo.packageName }.distinct()
        return pkgs.filter { it !in skip }.mapNotNull { pkg ->
            val pi = runCatching { pm.getPackageInfo(pkg, 0) }.getOrNull() ?: return@mapNotNull null
            val ai = pi.applicationInfo ?: return@mapNotNull null
            val system = ai.flags and ApplicationInfo.FLAG_SYSTEM != 0
            val updatedSystem = ai.flags and ApplicationInfo.FLAG_UPDATED_SYSTEM_APP != 0
            if (system && !updatedSystem) return@mapNotNull null
            mapOf(
                "package" to pkg,
                "label" to ai.loadLabel(pm).toString(),
                "version_name" to (pi.versionName ?: ""),
                "version_code" to ManagedApps.versionCode(pi),
                "updated_ms" to pi.lastUpdateTime,
                "icon" to runCatching { iconPng(ai, context) }.getOrNull(),
                "system" to system,
                "from_play" to (installerOf(context, pkg) == PLAY),
                "size_bytes" to apkBytes(ai),
            )
        }.sortedBy { (it["label"] as String).lowercase() }
    }

    @Suppress("DEPRECATION")
    private fun installerOf(context: Context, pkg: String): String? = runCatching {
        val pm = context.packageManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) pm.getInstallSourceInfo(pkg).installingPackageName
        else pm.getInstallerPackageName(pkg)
    }.getOrNull()

    /** הקובץ הראשי + הפיצולים (split APKs) — מה שתופס האפליקציה עצמה, בלי הנתונים שלה. */
    private fun apkBytes(ai: ApplicationInfo): Long =
        listOfNotNull(ai.sourceDir, *(ai.splitSourceDirs ?: emptyArray()))
            .sumOf { runCatching { File(it).length() }.getOrDefault(0L) }

    private fun iconPng(ai: ApplicationInfo, context: Context): String {
        val d = ai.loadIcon(context.packageManager)
        val bmp = Bitmap.createBitmap(ICON_PX, ICON_PX, Bitmap.Config.ARGB_8888)
        d.setBounds(0, 0, ICON_PX, ICON_PX)
        d.draw(Canvas(bmp))
        val out = ByteArrayOutputStream()
        bmp.compress(Bitmap.CompressFormat.PNG, 100, out)
        bmp.recycle()
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }

    /**
     * התקדמות התקנות בטלפון (גם של Google Play) — PackageInstaller.SessionCallback. מה שאנדרואיד
     * לא חושף (חבילה שלא גלויה לנו) פשוט לא מדווח; הממשק מזהה סיום גם לפי שינוי הגרסה.
     */
    class Progress(context: Context, private val emit: (Map<String, Any?>) -> Unit) : PackageInstaller.SessionCallback() {
        private val installer = context.packageManager.packageInstaller
        private val packages = HashMap<Int, String>()

        fun register() = installer.registerSessionCallback(this, Handler(Looper.getMainLooper()))
        fun unregister() = runCatching { installer.unregisterSessionCallback(this) }

        private fun send(id: Int, progress: Float?, state: String) {
            val pkg = packages[id] ?: runCatching { installer.getSessionInfo(id)?.appPackageName }.getOrNull()
                ?.also { packages[id] = it } ?: return
            emit(mapOf("package" to pkg, "progress" to progress?.toDouble(), "state" to state))
        }

        override fun onCreated(sessionId: Int) = send(sessionId, 0f, "running")
        override fun onBadgingChanged(sessionId: Int) = Unit
        override fun onActiveChanged(sessionId: Int, active: Boolean) = Unit
        override fun onProgressChanged(sessionId: Int, progress: Float) = send(sessionId, progress, "running")
        override fun onFinished(sessionId: Int, success: Boolean) {
            send(sessionId, 1f, if (success) "done" else "failed")
            packages.remove(sessionId)
        }
    }
}
