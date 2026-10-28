package com.filterphone.app

import android.app.Activity
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.Toast

/**
 * הדפדפן הפנימי של וואטסאפ בלי "פתיחה בדפדפן" בתפריט: המגן לחץ "העתקת הקישור" וסגר אותו, וכאן
 * הקישור נפתח ב-Chrome (שם הוא מסונן). את הלוח אפשר לקרוא רק מאפליקציה שבחזית עם פוקוס (אנדרואיד 10+),
 * ולכן מסך שקוף לרגע — בלי היסטוריה ובלי "אחרונים". רק קישורי http/https; שום דבר לא נשמר.
 */
class LinkRelayActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private var done = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        main.postDelayed({ if (!done) finish() }, GIVE_UP_MS)     // לא קיבלנו פוקוס — לא נשארים תקועים
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (!hasFocus || done) return
        done = true
        val link = runCatching {
            val clip = getSystemService(ClipboardManager::class.java)?.primaryClip
            if (clip != null && clip.itemCount > 0) WaBrowser.linkFrom(clip.getItemAt(0).coerceToText(this)) else null
        }.getOrNull()
        if (link == null) {
            Toast.makeText(this, R.string.relay_no_link, Toast.LENGTH_SHORT).show()
        } else if (openInChrome(this, link)) {
            Toast.makeText(this, R.string.guard_wa_browser_chrome, Toast.LENGTH_SHORT).show()
        }
        finish()
    }

    override fun onDestroy() {
        main.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    companion object {
        private const val TAG = "FilterPhone"
        private const val GIVE_UP_MS = 4_000L

        fun start(context: Context) {
            runCatching {
                context.startActivity(Intent(context, LinkRelayActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION))
            }.onFailure { Log.w(TAG, "link relay start failed", it) }
        }

        /** הקישור ב-Chrome (שעובר סינון מלא); Chrome לא זמין — בדפדפן ברירת המחדל. */
        fun openInChrome(context: Context, link: String): Boolean {
            val view = Intent(Intent.ACTION_VIEW, Uri.parse(link)).addCategory(Intent.CATEGORY_BROWSABLE)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            return runCatching { context.startActivity(Intent(view).setPackage(WaBrowser.CHROME)); true }
                .recoverCatching { context.startActivity(view); true }
                .onFailure { Log.w(TAG, "open link in chrome failed", it) }
                .getOrDefault(false)
        }
    }
}
