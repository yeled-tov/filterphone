package com.filterphone.crm

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent

/** ערוצי ההתראות וההתראות עצמן. */
object Notifier {
    const val CH_EVENTS = "events_v1"
    const val CH_SERVICE = "service_v1"
    private const val GROUP = "crm_events"
    const val EXTRA_ROUTE = "com.filterphone.crm.ROUTE"

    private fun nm(c: Context) = c.getSystemService(NotificationManager::class.java)

    fun channels(c: Context) {
        val nm = nm(c) ?: return
        nm.createNotificationChannel(NotificationChannel(CH_EVENTS, c.getString(R.string.channel_events),
            NotificationManager.IMPORTANCE_HIGH).apply {
            description = c.getString(R.string.channel_events_desc)
            enableVibration(true)
        })
        nm.createNotificationChannel(NotificationChannel(CH_SERVICE, c.getString(R.string.channel_service),
            NotificationManager.IMPORTANCE_MIN).apply {
            description = c.getString(R.string.channel_service_desc)
            setShowBadge(false)
        })
    }

    /** פותח את האפליקציה בעמוד המתאים בדשבורד. */
    private fun open(c: Context, route: String?, requestCode: Int): PendingIntent {
        val i = Intent(c, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        Crm.safeRoute(route)?.let { i.putExtra(EXTRA_ROUTE, it) }
        return PendingIntent.getActivity(c, requestCode, i,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    fun show(c: Context, events: List<Event>) {
        if (events.isEmpty()) return
        val nm = nm(c) ?: return
        channels(c)
        val color = c.getColor(R.color.accent)
        for (e in events) {
            val id = Events.notificationId(e.key)
            val n = Notification.Builder(c, CH_EVENTS)
                .setSmallIcon(R.drawable.ic_notify)
                .setColor(color)
                .setContentTitle(e.title)
                .setContentText(e.text)
                .setStyle(Notification.BigTextStyle().bigText(e.text))
                .setContentIntent(open(c, e.route, id))
                .setAutoCancel(true)
                .setCategory(if (e.kind == "support") Notification.CATEGORY_MESSAGE else Notification.CATEGORY_EVENT)
                .setGroup(GROUP)
                .apply { if (e.ts > 0) setWhen(e.ts * 1000).setShowWhen(true) }
                .build()
            nm.notify(id, n)
        }
        // כמה התראות — מקובצות תחת אחת (אנדרואיד מציג את הסיכום כשיש יותר מאחת)
        val active = nm.activeNotifications.count { it.notification.group == GROUP && it.id != Events.ID_SUMMARY }
        if (active > 1) {
            nm.notify(Events.ID_SUMMARY, Notification.Builder(c, CH_EVENTS)
                .setSmallIcon(R.drawable.ic_notify)
                .setColor(color)
                .setContentTitle(c.getString(R.string.app_name))
                .setContentText(c.getString(R.string.summary_text, active))
                .setContentIntent(open(c, null, Events.ID_SUMMARY))
                .setAutoCancel(true)
                .setGroup(GROUP)
                .setGroupSummary(true)
                .setGroupAlertBehavior(Notification.GROUP_ALERT_CHILDREN)
                .build())
        }
    }

    fun service(c: Context): Notification {
        channels(c)
        return Notification.Builder(c, CH_SERVICE)
            .setSmallIcon(R.drawable.ic_notify)
            .setColor(c.getColor(R.color.accent))
            .setContentTitle(c.getString(R.string.service_title))
            .setContentText(c.getString(R.string.service_text))
            .setContentIntent(open(c, null, Events.ID_SERVICE))
            .setOngoing(true)
            .setShowWhen(false)
            .build()
    }

    /** הכניסה פגה (30 יום) / יצאו מהדשבורד במכשיר אחר — פעם אחת, עד שמתחברים שוב. */
    fun relogin(c: Context) {
        if (Crm.reloginShown(c)) return
        val nm = nm(c) ?: return
        channels(c)
        nm.notify(Events.ID_RELOGIN, Notification.Builder(c, CH_EVENTS)
            .setSmallIcon(R.drawable.ic_notify)
            .setColor(c.getColor(R.color.accent))
            .setContentTitle(c.getString(R.string.relogin_title))
            .setContentText(c.getString(R.string.relogin_text))
            .setContentIntent(open(c, null, Events.ID_RELOGIN))
            .setAutoCancel(true)
            .build())
        Crm.setReloginShown(c, true)
    }

    fun cancelRelogin(c: Context) = nm(c)?.cancel(Events.ID_RELOGIN)

    fun cancelEvents(c: Context) {
        val nm = nm(c) ?: return
        nm.activeNotifications.filter { it.id != Events.ID_SERVICE }.forEach { nm.cancel(it.id) }
    }
}
