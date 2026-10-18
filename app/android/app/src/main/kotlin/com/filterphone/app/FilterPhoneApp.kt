package com.filterphone.app

import android.app.Application
import android.os.Build
import org.lsposed.hiddenapibypass.HiddenApiBypass
import javax.net.ssl.HttpsURLConnection

class FilterPhoneApp : Application() {
    override fun onCreate() {
        super.onCreate()
        // ניפוי אלחוטי מתוך האפליקציה (libadb) ניגש ל-API נסתר של אנדרואיד — פותחים את הגישה מוקדם.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            runCatching { HiddenApiBypass.addHiddenApiExemptions("") }
        }
        // ברשת כשרה (נטפרי וכו') הספק פותח HTTPS עם התעודה שלו — הבקשות שלנו לשרת סומכות גם עליה
        runCatching { HttpsURLConnection.setDefaultSSLSocketFactory(KosherNet.socketFactory(this)) }
        // חייב לרוץ מוקדם: במצב "VPN תמיד מופעל" המערכת מפעילה את השירות בלי פתיחת האפליקציה
        TunnelManager.init(this)
        ProtectionService.createChannel(this)
        // המשימה התקופתית (דיווח + בדיקת VPN) — מתוזמנת פעם אחת ונשמרת גם אחרי אתחול
        if (TunnelManager.wantsConnection() || GuardPolicy.credentials(this) != null) Recovery.scheduleDaily(this)
        // מגן FilterPhone (נגישות) — אם יש הרשאה, חוזר מיד כשמכבים אותו
        AccessibilityGuard.watch(this)
        Recovery.runAsync(this) { AccessibilityGuard.ensureEnabled(this, "app start") }
    }
}
