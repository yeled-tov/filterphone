package com.filterphone.app

import android.app.Application

class FilterPhoneApp : Application() {
    override fun onCreate() {
        super.onCreate()
        // חייב לרוץ מוקדם: במצב "VPN תמיד מופעל" המערכת מפעילה את השירות בלי פתיחת האפליקציה
        TunnelManager.init(this)
        ProtectionService.createChannel(this)
        // המשימה התקופתית (דיווח + בדיקת VPN) — מתוזמנת פעם אחת ונשמרת גם אחרי אתחול
        if (TunnelManager.wantsConnection() || GuardPolicy.credentials(this) != null) Recovery.schedulePeriodic(this)
    }
}
