package com.filterphone.app

import android.app.Application

class FilterPhoneApp : Application() {
    override fun onCreate() {
        super.onCreate()
        // חייב לרוץ מוקדם: במצב "VPN תמיד מופעל" המערכת מפעילה את השירות בלי פתיחת האפליקציה
        TunnelManager.init(this)
    }
}
