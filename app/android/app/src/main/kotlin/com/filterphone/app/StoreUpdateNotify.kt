package com.filterphone.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import org.json.JSONArray

/**
 * בלי מנהל מכשיר אין התקנה שקטה: כשלאפליקציה מהחנות (FilterTube וכו') יש גרסה חדשה אצלנו — התראה אחת
 * לכל גרסה (לא כל 15 דקות). לחיצה פותחת את FilterPhone בחנות, ושם "עדכון" מוריד ומבקש אישור התקנה.
 */
object StoreUpdateNotify {
    private const val CHANNEL_ID = "store_updates"
    private const val NOTIFICATION_ID = 7301
    private const val PREFS = "fp_store_updates"
    private const val K_SEEN = "seen"
    const val OPEN_STORE = "store"

    /** מפתח של מה שכבר התרענו עליו: "pkg:version" ממוין. טהור. */
    fun key(updates: List<ManagedApps.App>): String =
        updates.map { "${it.pkg}:${it.versionCode}" }.sorted().joinToString(",")

    fun onUpdates(context: Context, updates: List<ManagedApps.App>, catalog: JSONArray?) {
        val p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        val k = key(updates)
        if (updates.isEmpty()) {
            if (p.getString(K_SEEN, "").orEmpty().isNotEmpty()) nm.cancel(NOTIFICATION_ID)
            p.edit().putString(K_SEEN, "").apply()
            return
        }
        if (k == p.getString(K_SEEN, null)) return
        p.edit().putString(K_SEEN, k).apply()
        if (nm.getNotificationChannel(CHANNEL_ID) == null) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL_ID, context.getString(R.string.store_update_channel),
                NotificationManager.IMPORTANCE_DEFAULT))
        }
        val title = if (updates.size == 1) {
            context.getString(R.string.store_update_title, label(catalog, updates[0].pkg))
        } else {
            context.getString(R.string.store_update_title_many, updates.size)
        }
        val open = (context.packageManager.getLaunchIntentForPackage(context.packageName) ?: return)
            .putExtra(SupportNotify.EXTRA_OPEN, OPEN_STORE)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val pi = PendingIntent.getActivity(context, NOTIFICATION_ID, open,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        runCatching {
            nm.notify(NOTIFICATION_ID, Notification.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_shield)
                .setContentTitle(title)
                .setContentText(context.getString(R.string.store_update_text))
                .setAutoCancel(true)
                .setContentIntent(pi)
                .build())
        }
    }

    private fun label(catalog: JSONArray?, pkg: String): String {
        for (i in 0 until (catalog?.length() ?: 0)) {
            val o = catalog!!.optJSONObject(i) ?: continue
            if (o.optString("package") == pkg) return o.optString("label").ifBlank { pkg }
        }
        return pkg
    }
}
