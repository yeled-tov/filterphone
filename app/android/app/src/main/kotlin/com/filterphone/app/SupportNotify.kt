package com.filterphone.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import org.json.JSONObject

/**
 * "יש תשובה מהתמיכה": השרת מחזיר בדיווח התקופתי {unread, last_id} כשהמנהל ענה ועוד לא נקרא.
 * התראה אחת לכל תשובה חדשה (לא חוזרת כל 15 דקות). בלי תוכן ההודעה במסך הנעילה — רק שיש תשובה.
 * לחיצה פותחת את האפליקציה ישר בלשונית התמיכה.
 */
object SupportNotify {
    private const val CHANNEL_ID = "support"
    private const val NOTIFICATION_ID = 7201
    private const val PREFS = "fp_support"
    private const val K_NOTIFIED = "notified_id"
    const val EXTRA_OPEN = "com.filterphone.app.extra.OPEN"
    const val OPEN_SUPPORT = "support"

    /** האם להתריע: יש לא-נקראות, ומזהה ההודעה האחרונה חדש ממה שכבר התרענו עליו. טהור. */
    fun shouldNotify(unread: Int, lastId: Long, notifiedId: Long): Boolean = unread > 0 && lastId > notifiedId

    fun onServer(context: Context, support: JSONObject?) {
        val unread = support?.optInt("unread", 0) ?: 0
        val lastId = support?.optLong("last_id", 0L) ?: 0L
        val p = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (!shouldNotify(unread, lastId, p.getLong(K_NOTIFIED, 0L))) return
        p.edit().putLong(K_NOTIFIED, lastId).apply()
        show(context, unread)
    }

    private fun show(context: Context, unread: Int) {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CHANNEL_ID) == null) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL_ID, context.getString(R.string.support_channel),
                NotificationManager.IMPORTANCE_DEFAULT))
        }
        val open = (context.packageManager.getLaunchIntentForPackage(context.packageName) ?: return)
            .putExtra(EXTRA_OPEN, OPEN_SUPPORT)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val pi = PendingIntent.getActivity(context, NOTIFICATION_ID, open,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val text = if (unread > 1) context.getString(R.string.support_replies, unread)
        else context.getString(R.string.support_reply)
        runCatching {
            nm.notify(NOTIFICATION_ID, Notification.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_shield)
                .setContentTitle(context.getString(R.string.support_title))
                .setContentText(text)
                .setAutoCancel(true)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .setCategory(Notification.CATEGORY_MESSAGE)
                .setContentIntent(pi)
                .build())
        }
    }

    /** המשתמש פתח את הצ'אט — ההתראה כבר לא רלוונטית. */
    fun clear(context: Context) {
        context.getSystemService(NotificationManager::class.java)?.cancel(NOTIFICATION_ID)
    }
}
