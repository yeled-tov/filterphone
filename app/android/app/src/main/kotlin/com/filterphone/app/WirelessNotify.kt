package com.filterphone.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.RemoteInput
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log

/**
 * הזנת קוד ההתאמה (pairing) של "ניפוי אלחוטי" מתוך התראה — כמו בסוויץ'.
 *
 * אנדרואיד מציג את קוד ההתאמה בחלון קופץ מעל מסך "ניפוי אלחוטי". אם יוצאים מהמסך הזה הקוד מתחלף,
 * ולכן אי אפשר לעבור לאפליקציה ולהקליד. הפתרון: התראה עם שדה קלט (RemoteInput) — מורידים את פס
 * ההתראות *בלי לצאת מהמסך*, מקלידים את הקוד בשורת ההתראה, שולחים, וההקמה ממשיכה לבד ברקע.
 *
 * ה-CA (תעודת הסינון) נשמר על ההתראה עצמה, כך שהמקלט יכול להתקין אותה בסוף בלי השרת.
 */
object WirelessNotify {
    const val CHANNEL_ID = "wireless_setup"
    const val NOTIF_ID = 7311
    const val ACTION_CODE = "com.filterphone.app.WIRELESS_CODE"
    const val KEY_CODE = "fp_code"
    const val EXTRA_CA = "fp_ca"
    private const val TAG = "FilterPhone"

    private fun nm(context: Context) = context.getSystemService(NotificationManager::class.java)

    private fun ensureChannel(context: Context) {
        val nm = nm(context) ?: return
        if (nm.getNotificationChannel(CHANNEL_ID) != null) return
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "הקמת FilterPhone (ניפוי אלחוטי)", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "הזנת קוד ההתאמה להקמה בלי מחשב"
                setShowBadge(false)
            },
        )
    }

    /** מציג את התראת הזנת הקוד. מחזיר false אם התראות כבויות (ואז הממשק מציע הזנה ידנית). */
    fun prompt(context: Context, caPem: String?, error: String? = null): Boolean {
        val mgr = nm(context) ?: return false
        if (!mgr.areNotificationsEnabled()) return false
        ensureChannel(context)
        val remoteInput = RemoteInput.Builder(KEY_CODE)
            .setLabel("קוד בן 6 ספרות")
            .build()
        val intent = Intent(context, WirelessCodeReceiver::class.java)
            .setAction(ACTION_CODE)
            .setPackage(context.packageName)
            .putExtra(EXTRA_CA, caPem)
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or
            (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0)
        val pi = PendingIntent.getBroadcast(context, 7312, intent, flags)
        val action = Notification.Action.Builder(0, "שליחת הקוד", pi)
            .addRemoteInput(remoteInput)
            .build()
        val text = error
            ?: "משכו מכאן את פס ההתראות, הקלידו את קוד ההתאמה שמופיע במסך \"ניפוי אלחוטי\", ושלחו — ההקמה תמשיך לבד."
        val n = Notification.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_shield)
            .setContentTitle(if (error != null) "הקוד לא התקבל — נסו שוב" else "FilterPhone — הזנת קוד ההתאמה")
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setOngoing(true)
            .setOnlyAlertOnce(error == null)
            .addAction(action)
            .build()
        mgr.notify(NOTIF_ID, n)
        return true
    }

    /** מצב ביניים בזמן ההתאמה/ההקמה. */
    fun progress(context: Context, text: String) {
        ensureChannel(context)
        val n = Notification.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_shield)
            .setContentTitle("FilterPhone — מקים את ההגנה…")
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setProgress(0, 0, true)
            .build()
        nm(context)?.notify(NOTIF_ID, n)
    }

    /** תוצאה סופית. בהצלחה — התראה קצרה שתיעלם; בכישלון — חוזרים לבקש קוד. */
    fun done(context: Context, ok: Boolean, text: String) {
        ensureChannel(context)
        val n = Notification.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_shield)
            .setContentTitle(if (ok) "FilterPhone — ההגנה הופעלה ✓" else "ההקמה לא הושלמה")
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setOngoing(false)
            .setAutoCancel(true)
            .build()
        nm(context)?.notify(NOTIF_ID, n)
    }

    fun cancel(context: Context) {
        nm(context)?.cancel(NOTIF_ID)
    }
}

/**
 * מקבל את קוד ההתאמה מההתראה ומריץ את ההקמה האלחוטית ברקע. לא חשוף לאפליקציות אחרות.
 */
class WirelessCodeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != WirelessNotify.ACTION_CODE) return
        val code = RemoteInput.getResultsFromIntent(intent)?.getCharSequence(WirelessNotify.KEY_CODE)
            ?.toString()?.filter(Char::isDigit).orEmpty()
        val caPem = intent.getStringExtra(WirelessNotify.EXTRA_CA)
        if (!WirelessSetup.validCode(code)) {
            WirelessNotify.prompt(context, caPem, error = "צריך קוד בן 6 ספרות. הקלידו שוב את הקוד מהמסך.")
            return
        }
        WirelessNotify.progress(context, "מתאים את המכשיר ומפעיל את ההגנה. אפשר לחזור למסך הבית.")
        val app = context.applicationContext
        val pending = goAsync()
        Thread {
            try {
                val report = OneTapSetup.runWireless(app, code, 0, caPem)
                val owner = report["device_owner"] == true
                val pairingOk = report["pairing_ok"] == true
                if (owner) {
                    Recovery.startProtection(app)
                    WirelessNotify.done(app, true, "המכשיר מוגדר כעת כמנהל מכשיר והסינון פעיל.")
                } else if (!pairingOk) {
                    WirelessNotify.prompt(app, caPem, error = "הקוד לא התאים או שפג תוקפו. לחצו שוב \"התאמת מכשיר באמצעות קוד\" והקלידו קוד חדש.")
                } else {
                    WirelessNotify.done(app, false, "ההתאמה הצליחה אך ההגדרה לא הושלמה. פתחו את FilterPhone להמשך.")
                }
            } catch (e: Exception) {
                Log.w("FilterPhone", "wireless code provisioning failed", e)
                WirelessNotify.prompt(app, caPem, error = "משהו השתבש. ודאו ש\"ניפוי אלחוטי\" פעיל ונסו שוב עם קוד חדש.")
            } finally {
                pending.finish()
            }
        }.apply { isDaemon = true }.start()
    }
}
