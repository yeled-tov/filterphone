package com.filterphone.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import com.wireguard.android.backend.Tunnel

/**
 * "הגנת FilterPhone פעילה" — שירות קדמי עם התראה קבועה (שקטה).
 *
 * למה צריך אותו, אם שירות ה-VPN עצמו כבר רץ: כשהמנהרה נופלת (רשת התחלפה, השרת לא ענה),
 * שירות ה-VPN נעלם — ואיתו העדיפות של התהליך. השירות הזה נשאר, מקבל מיד אירועי רשת,
 * ומחבר מחדש (דרך ה-Backoff — בלי לולאה). בלי טיימר תקופתי שמעיר את המעבד: הבדיקה מונחית-אירועים —
 * שינוי רשת והדלקת מסך (בדיוק כשהמשתמש עומד לגלוש). כך כמעט בלי סוללה בזמן שהטלפון במנוחה.
 * ההתראה מראה את המצב האמיתי: מוגן / מתחבר מחדש / נדרשת פעולה.
 *
 * אנדרואיד 14+: סוג specialUse (סינון תוכן — אין סוג ייעודי ל"שומר VPN"). START_STICKY:
 * אם המערכת הורגת את התהליך, היא מפעילה את השירות מחדש כשאפשר.
 */
class ProtectionService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private var networkCallback: ConnectivityManager.NetworkCallback? = null
    private val tunnelListener: (Tunnel.State) -> Unit = { main.post { updateNotification() } }

    /** המשתמש הדליק מסך — בדיוק הרגע שחשוב שהסינון יהיה פעיל. בדיקה זולה (מחברת רק אם צריך). */
    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == Intent.ACTION_SCREEN_ON) checkHealth("screen")
        }
    }

    private fun checkHealth(why: String) {
        Recovery.runAsync(applicationContext) {
            TunnelManager.healthCheck()
            main.post { updateNotification() }
        }
    }

    override fun onCreate() {
        super.onCreate()
        TunnelManager.init(this)
        createChannel(this)
        startInForeground()
        running = true
        TunnelManager.addListener(tunnelListener)
        registerNetworkCallback()
        PackageWatch.register(this)          // FilterTube הותקנה/הוסרה — המנהרה נבנית מחדש מיד
        runCatching { registerReceiver(screenReceiver, IntentFilter(Intent.ACTION_SCREEN_ON)) }
        checkHealth("start")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startInForeground()
        if (!TunnelManager.wantsConnection()) {
            stopSelf()
            return START_NOT_STICKY
        }
        Recovery.runAsync(applicationContext) {
            TunnelManager.ensureConnected("service")
            main.post { updateNotification() }
        }
        return START_STICKY
    }

    private fun startInForeground() {
        val n = buildNotification(this)
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
            } else {
                startForeground(NOTIFICATION_ID, n)
            }
        } catch (e: Exception) {
            // לא הותר (רקע בלי פטור) — ההתאוששות ממשיכה דרך JobScheduler
            Log.w(TAG, "startForeground failed: ${e.javaClass.simpleName}")
            stopSelf()
        }
    }

    private fun registerNetworkCallback() {
        val cm = getSystemService(ConnectivityManager::class.java) ?: return
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) = onNetworkChange("available")
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                if (caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)) onNetworkChange("validated")
            }
            override fun onLost(network: Network) {
                main.post { updateNotification() }
            }
        }
        try {
            cm.registerDefaultNetworkCallback(cb)
            networkCallback = cb
        } catch (e: Exception) {
            Log.w(TAG, "network callback not registered", e)
        }
    }

    private var lastNetworkKick = 0L

    private fun onNetworkChange(why: String) {
        val now = System.currentTimeMillis()
        if (now - lastNetworkKick < 3_000) return       // סדרת אירועים מאותו שינוי — פעם אחת
        lastNetworkKick = now
        if (TunnelManager.state == Tunnel.State.UP) return
        Recovery.runAsync(applicationContext) {
            TunnelManager.ensureConnected("network $why")
            main.post { updateNotification() }
        }
    }

    private fun updateNotification() {
        runCatching {
            getSystemService(NotificationManager::class.java)?.notify(NOTIFICATION_ID, buildNotification(this))
        }
    }

    override fun onDestroy() {
        running = false
        runCatching { unregisterReceiver(screenReceiver) }
        PackageWatch.unregister(this)
        TunnelManager.removeListener(tunnelListener)
        networkCallback?.let { cb ->
            runCatching { getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(cb) }
        }
        networkCallback = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val TAG = "FilterPhone"
        const val CHANNEL_ID = "protection"
        const val NOTIFICATION_ID = 7101
        @Volatile private var running = false

        /** עדכון ההתראה הקבועה (למשל בתחילת/סוף השהיה) — רק כשהשירות רץ. */
        fun refresh(context: Context) {
            if (!running) return
            runCatching {
                context.getSystemService(NotificationManager::class.java)?.notify(NOTIFICATION_ID, buildNotification(context))
            }
        }

        fun createChannel(context: Context) {
            val nm = context.getSystemService(NotificationManager::class.java) ?: return
            if (nm.getNotificationChannel(CHANNEL_ID) != null) return
            val ch = NotificationChannel(CHANNEL_ID, context.getString(R.string.protection_channel),
                NotificationManager.IMPORTANCE_LOW).apply {
                description = context.getString(R.string.protection_channel_desc)
                setShowBadge(false)
            }
            nm.createNotificationChannel(ch)
        }

        fun buildNotification(context: Context): Notification {
            val text = when {
                AdminState.isPaused(context) -> R.string.protection_paused
                !TunnelManager.isPermissionGranted() -> R.string.protection_needs_permission
                TunnelManager.state == Tunnel.State.UP -> R.string.protection_on
                else -> R.string.protection_reconnecting
            }
            val open = context.packageManager.getLaunchIntentForPackage(context.packageName)
            val pi = open?.let {
                PendingIntent.getActivity(context, 0, it, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            }
            return Notification.Builder(context, CHANNEL_ID).setSmallIcon(R.drawable.ic_stat_shield)
                .setContentTitle(context.getString(R.string.protection_title))
                .setContentText(context.getString(text))
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setShowWhen(false)
                .setCategory(Notification.CATEGORY_SERVICE)
                .apply { if (pi != null) setContentIntent(pi) }
                .build()
        }
    }
}
