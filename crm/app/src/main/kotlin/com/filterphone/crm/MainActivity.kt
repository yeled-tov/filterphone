package com.filterphone.crm

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.util.Log
import android.view.WindowInsets
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.window.OnBackInvokedDispatcher
import org.json.JSONObject

/**
 * CRM FilterPhone — הדשבורד של FilterPhone כאפליקציה (רכיב ה-WebView של המערכת, בלי Chrome ובלי דפדפן).
 *
 *   • רק הדשבורד שלנו נטען כאן (api.filterphone.com, https). קישור לאתר אחר נפתח מחוץ לאפליקציה.
 *   • הכניסה נשמרת מוצפנת בטלפון (30 יום) דרך הגשר FPApp — והשירות משתמש בה להתראות.
 *   • לחיצה על התראה פותחת את העמוד המתאים (#/support/7, #/requests...).
 *   • העלאת קבצים (APK לחנות, תמונה, תעודה) — בוחר הקבצים של המערכת. "אחורה" — אחורה בדשבורד, ואז יציאה.
 */
class MainActivity : Activity() {
    private lateinit var web: WebView
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    @SuppressLint("SetJavaScriptEnabled", "AddJavascriptInterface")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Notifier.channels(this)
        web = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.setSupportZoom(false)
            settings.setSupportMultipleWindows(false)
            settings.userAgentString = settings.userAgentString + " CrmFilterPhone/1"
            addJavascriptInterface(Bridge(this@MainActivity), "FPApp")
            webViewClient = Client()
            webChromeClient = Chrome()
            setDownloadListener { url, _, _, _, _ -> openOutside(Uri.parse(url)) }
        }
        val root = FrameLayout(this).apply {
            setBackgroundColor(getColor(R.color.bg))
            addView(web, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        }
        setContentView(root)
        fitInsets(root)
        if (Build.VERSION.SDK_INT >= 33) {
            onBackInvokedDispatcher.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT) { back() }
        }
        val route = intent?.getStringExtra(Notifier.EXTRA_ROUTE)
        if (route == null && savedInstanceState != null && web.restoreState(savedInstanceState) != null) {
            // ממשיכים מאותו מקום
        } else {
            web.loadUrl(Crm.dashboardUrl(route))
        }
        askNotifications()
        if (Crm.token(this) != null) WatchService.start(this)
    }

    /** אנדרואיד 11+: מסך מלא, והריווח (פס סטטוס, פס ניווט, מקלדת) — כאן, כדי שהדשבורד לא יוסתר. */
    private fun fitInsets(root: FrameLayout) {
        if (Build.VERSION.SDK_INT < 30) return       // עד 10: המערכת עושה את זה לבד (adjustResize)
        window.setDecorFitsSystemWindows(false)
        root.setOnApplyWindowInsetsListener { v, insets ->
            val i = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.ime() or
                WindowInsets.Type.displayCutout())
            v.setPadding(i.left, i.top, i.right, i.bottom)
            WindowInsets.CONSUMED
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val route = Crm.safeRoute(intent.getStringExtra(Notifier.EXTRA_ROUTE)) ?: return
        if (ours(web.url?.let(Uri::parse))) {
            web.evaluateJavascript("location.hash = ${JSONObject.quote(route)}", null)
        } else {
            web.loadUrl(Crm.dashboardUrl(route))
        }
    }

    override fun onResume() {
        super.onResume()
        if (Crm.token(this) != null) WatchService.start(this, now = true)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (::web.isInitialized) web.saveState(outState)
    }

    override fun onDestroy() {
        if (::web.isInitialized) {
            web.removeJavascriptInterface("FPApp")
            web.destroy()
        }
        super.onDestroy()
    }

    // עד אנדרואיד 12; מ-13 — onBackInvokedDispatcher למעלה
    @SuppressLint("GestureBackNavigation")
    @Deprecated("Deprecated in Java")
    override fun onBackPressed() = back()

    private fun back() {
        if (::web.isInitialized && web.canGoBack()) web.goBack() else finish()
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_FILE) return
        fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data))
        fileCallback = null
    }

    private fun askNotifications() {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFY)
        }
    }

    /** פעם אחת, אחרי הכניסה: שחיסכון הסוללה לא יעצור את ההתראות (חלון אישור של המערכת). */
    @SuppressLint("BatteryLife")
    private fun askBatteryOnce() {
        if (Crm.askedBattery(this)) return
        Crm.setAskedBattery(this)
        val pm = getSystemService(PowerManager::class.java) ?: return
        if (pm.isIgnoringBatteryOptimizations(packageName)) return
        try {
            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
        } catch (e: Exception) {
            Log.w(Crm.TAG, "battery optimization prompt unavailable: ${e.javaClass.simpleName}")
        }
    }

    private fun ours(uri: Uri?): Boolean = uri != null && uri.scheme == "https" && uri.host == Crm.HOST

    private fun openOutside(uri: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: ActivityNotFoundException) {
            Log.w(Crm.TAG, "no app for ${uri.scheme}")
        }
    }

    private inner class Client : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            if (ours(request.url)) return false
            openOutside(request.url)     // אתר אחר / וואטסאפ / טלפון — מחוץ לאפליקציה
            return true
        }

        override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
            if (url != null && !ours(Uri.parse(url)) && url != "about:blank" && !url.startsWith("data:")) view.stopLoading()
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            Log.w(Crm.TAG, "dashboard load failed: ${error.errorCode}")
            view.loadDataWithBaseURL(null, offlinePage(), "text/html", "utf-8", null)
        }
    }

    private inner class Chrome : WebChromeClient() {
        override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
            fileCallback?.onReceiveValue(null)
            fileCallback = callback
            return try {
                @Suppress("DEPRECATION")
                startActivityForResult(params.createIntent(), REQ_FILE)
                true
            } catch (e: ActivityNotFoundException) {
                fileCallback = null
                false
            }
        }
    }

    /** "אין חיבור" — בעברית, עם כפתור גדול שטוען שוב את הדשבורד. */
    private fun offlinePage(): String = """
        <!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light dark">
        <style>body{font:17px/1.6 system-ui,sans-serif;margin:0;min-height:100vh;display:grid;place-items:center;
        text-align:center;padding:24px;box-sizing:border-box}button{font:inherit;font-weight:600;padding:14px 28px;
        border-radius:12px;border:0;background:#1f6feb;color:#fff;margin-top:18px;min-width:60%}</style></head>
        <body><div><h2>${getString(R.string.offline_title)}</h2><p>${getString(R.string.offline_text)}</p>
        <button onclick="location.href='${Crm.dashboardUrl()}'">${getString(R.string.offline_retry)}</button>
        </div></body></html>
    """.trimIndent()

    /**
     * הגשר לדשבורד (admin.js): שמירת הכניסה מוצפנת בטלפון. רק הדשבורד שלנו נטען בחלון הזה (Client),
     * ו-CSP של הדשבורד לא מרשה מסגרות מאתרים אחרים — כך שרק הוא יכול לקרוא לכאן.
     */
    private class Bridge(activity: MainActivity) {
        private val activity = java.lang.ref.WeakReference(activity)
        private val app: Context = activity.applicationContext

        @JavascriptInterface
        fun token(): String = Crm.token(app).orEmpty()

        @JavascriptInterface
        fun saveToken(token: String?) {
            if (!Crm.validToken(token)) return
            Crm.setToken(app, token)
            Notifier.cancelRelogin(app)
            WatchService.start(app, now = true)
            activity.get()?.let { a -> a.runOnUiThread { a.askBatteryOnce() } }
        }

        @JavascriptInterface
        fun clearToken() {
            Crm.setToken(app, null)
            WatchService.stop(app)
            Notifier.cancelEvents(app)
        }
    }

    private companion object {
        const val REQ_FILE = 7401
        const val REQ_NOTIFY = 7402
    }
}
