package com.filterphone.crm

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * גרסה חדשה של האפליקציה — לטלפון רגיל (שהותקנה בו מהקישור): פעם ב-6 שעות בודקים את CRM.json בדף ההורדה,
 * ואם יש גרסה חדשה — התראה שפותחת את ההורדה. בטלפון עם FilterPhone היא מתעדכנת לבד (FilterPhone מתקין) —
 * שם לא מציגים כלום.
 */
object Updates {
    private const val CHECK_EVERY_MS = 6 * 3600_000L
    const val APK_URL = "${Crm.BASE}/download/CRM-FilterPhone.apk"
    private const val FILTERPHONE = "com.filterphone.app"

    /** version_code מתוך CRM.json; -1 אם אין. טהור. */
    fun parseVersion(json: String): Long = try {
        JSONObject(json).optLong("version_code", -1)
    } catch (e: Exception) {
        -1
    }

    /** להודיע? רק על גרסה חדשה יותר, ופעם אחת לכל גרסה. טהור. */
    fun shouldNotify(latest: Long, current: Long, alreadyNotified: Long): Boolean =
        latest > current && latest != alreadyNotified

    fun maybeCheck(context: Context) {
        val c = context.applicationContext
        val now = System.currentTimeMillis()
        if (now - Crm.lastUpdateCheck(c) < CHECK_EVERY_MS) return
        Crm.setLastUpdateCheck(c, now)
        if (installer(c) == FILTERPHONE) return
        val latest = try {
            fetch("${Crm.BASE}/download/CRM.json")?.let(::parseVersion) ?: return
        } catch (e: Exception) {
            Log.w(Crm.TAG, "update check failed: ${e.javaClass.simpleName}")
            return
        }
        if (shouldNotify(latest, currentVersion(c), Crm.notifiedVersion(c))) {
            Notifier.update(c)
            Crm.setNotifiedVersion(c, latest)
        }
    }

    private fun fetch(url: String): String? {
        val conn = URL(url).openConnection() as HttpURLConnection
        return try {
            conn.connectTimeout = 15_000
            conn.readTimeout = 15_000
            if (conn.responseCode != 200) null else conn.inputStream.bufferedReader().use { it.readText() }
        } finally {
            conn.disconnect()
        }
    }

    private fun installer(c: Context): String? = try {
        if (Build.VERSION.SDK_INT >= 30) c.packageManager.getInstallSourceInfo(c.packageName).installingPackageName
        else @Suppress("DEPRECATION") c.packageManager.getInstallerPackageName(c.packageName)
    } catch (e: Exception) {
        null
    }

    private fun currentVersion(c: Context): Long = try {
        val info = c.packageManager.getPackageInfo(c.packageName, 0)
        if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()
    } catch (e: PackageManager.NameNotFoundException) {
        Long.MAX_VALUE
    }
}
