package com.filterphone.app

import android.app.Activity
import android.app.admin.DevicePolicyManager
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.provider.Settings
import android.security.KeyChain
import android.util.Base64
import java.security.KeyStore
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate

/**
 * התקנת תעודת הסינון (FilterPhone CA) ובדיקה שהיא מותקנת.
 *
 * אנדרואיד 11+ לא מאפשר לאפליקציה להציג חלון התקנה לתעודת CA — המשתמש מתקין
 * בהגדרות. לכן: שומרים את הקובץ בהורדות ופותחים את ההגדרות עם הסבר.
 * כשהאפליקציה היא "מנהל המכשיר" (Device Owner) — ההתקנה שקטה ומיידית.
 */
object CertHelper {
    const val FILE_NAME = "FilterPhone-CA.crt"

    fun pemToDer(pem: String): ByteArray =
        Base64.decode(pem.lines().filterNot { it.startsWith("-----") }.joinToString(""), Base64.DEFAULT)

    fun isInstalled(pem: String): Boolean {
        val target = CertificateFactory.getInstance("X.509")
            .generateCertificate(pemToDer(pem).inputStream()) as X509Certificate
        val store = KeyStore.getInstance("AndroidCAStore").apply { load(null) }
        for (alias in store.aliases()) {
            if (store.getCertificate(alias) == target) return true
        }
        return false
    }

    /** מחזיר איך ההתקנה מתבצעת: "installed" / "dialog" / "settings". */
    fun install(activity: Activity, pem: String, requestCode: Int): String {
        val der = pemToDer(pem)
        if (DeviceAdmin.isDeviceOwner(activity)) {
            val dpm = activity.getSystemService(DevicePolicyManager::class.java)
            if (dpm.installCaCert(DeviceAdmin.component(activity), der)) return "installed"
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            val intent = KeyChain.createInstallIntent()
                .putExtra(KeyChain.EXTRA_CERTIFICATE, der)
                .putExtra(KeyChain.EXTRA_NAME, "FilterPhone CA")
            activity.startActivityForResult(intent, requestCode)
            return "dialog"
        }
        saveToDownloads(activity, pem)
        openSecuritySettings(activity)
        return "settings"
    }

    /** שומר את התעודה בתיקיית ההורדות (מחליף עותק קודם). */
    fun saveToDownloads(context: Context, pem: String) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
        val resolver = context.contentResolver
        val collection = MediaStore.Downloads.EXTERNAL_CONTENT_URI
        resolver.delete(collection, "${MediaStore.MediaColumns.DISPLAY_NAME} = ?", arrayOf(FILE_NAME))
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, FILE_NAME)
            put(MediaStore.MediaColumns.MIME_TYPE, "application/x-x509-ca-cert")
            put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
        }
        val uri = resolver.insert(collection, values) ?: return
        resolver.openOutputStream(uri)?.use { it.write(pem.toByteArray(Charsets.US_ASCII)) }
    }

    /** מנסה לפתוח ישר את מסך "הצפנה ופרטי כניסה"; אם אין — את מסך האבטחה הכללי. */
    fun openSecuritySettings(activity: Activity) {
        val candidates = listOf(
            Intent("android.settings.ENCRYPTION_AND_CREDENTIAL_SETTINGS"),
            Intent("com.android.settings.SECURITY_ADVANCED_SETTINGS"),
            Intent(Settings.ACTION_SECURITY_SETTINGS),
        )
        for (intent in candidates) {
            try {
                activity.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                return
            } catch (_: Exception) {
                // המסך לא קיים ביצרן הזה — ממשיכים לבא בתור
            }
        }
    }
}
