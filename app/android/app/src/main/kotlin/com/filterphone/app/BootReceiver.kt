package com.filterphone.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import java.util.concurrent.TimeUnit

/** אחרי אתחול או עדכון של האפליקציה — מחבר את ה-VPN מחדש אם הוא היה מחובר. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        TunnelManager.init(context)
        if (!TunnelManager.wantsConnection() || !TunnelManager.hasConfig() || !TunnelManager.isPermissionGranted()) return
        val pending = goAsync()
        Thread {
            try {
                TunnelManager.connect().get(25, TimeUnit.SECONDS)
            } catch (e: Exception) {
                Log.e("FilterPhone", "boot reconnect failed", e)
            } finally {
                pending.finish()
            }
        }.start()
    }
}
