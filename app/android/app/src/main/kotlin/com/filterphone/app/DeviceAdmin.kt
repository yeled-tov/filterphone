package com.filterphone.app

import android.app.admin.DeviceAdminReceiver
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context

/**
 * "מנהל המכשיר" (Device Owner). מוכן מראש לשלב הנעילה: כשהאפליקציה מוגדרת כמנהל
 * (adb shell dpm set-device-owner com.filterphone.app/.DeviceAdmin) אפשר להתקין את
 * התעודה בשקט, לקבע VPN תמיד-פעיל עם חסימה, ולמנוע הסרה ואיפוס.
 * את מדיניות הנעילה עצמה נוסיף בשלב הבא — עם מנגנון שחרור מאובטח מהשרת.
 */
class DeviceAdmin : DeviceAdminReceiver() {
    companion object {
        fun component(context: Context) = ComponentName(context, DeviceAdmin::class.java)

        fun isDeviceOwner(context: Context): Boolean =
            context.getSystemService(DevicePolicyManager::class.java).isDeviceOwnerApp(context.packageName)
    }
}
