package com.filterphone.app

import android.annotation.SuppressLint
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.window.OnBackInvokedDispatcher

/**
 * "FilterPhone ניהול" — הדשבורד כאפליקציה בטלפון של המנהל: בלי Chrome ובלי דפדפן (רכיב ה-WebView של
 * המערכת), עם סמל משלו במסך הבית (AdminLauncher — נדלק רק במכשיר שהשרת סימן כמכשיר המנהל), כניסה שנשמרת
 * מוצפנת בטלפון (30 יום — SecureStore), ועיצוב שמותאם למסך קטן (שורת ניווט תחתונה, כפתורים גדולים) וגדול.
 *
 *   • רק הדשבורד שלנו (אותו שרת): קישור לאתר אחר נפתח בסייר המוגן — לא כאן.
 *   • העלאת קבצים (APK לחנות, תמונה, תעודה) — בוחר הקבצים של המערכת.
 *   • "אחורה" — אחורה בדשבורד, ואז יציאה.
 *   • אין רשת — דף קצר עם "נסה שוב", במקום דף השגיאה של ה-WebView.
 */
class AdminDashboardActivity : Activity() {
    private lateinit var web: WebView
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private val base: String by lazy {
        (GuardPolicy.credentials(this)?.first ?: DEFAULT_BASE).trimEnd('/')
    }
    private val host: String by lazy { Uri.parse(base).host.orEmpty() }

    @SuppressLint("SetJavaScriptEnabled", "AddJavascriptInterface")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this).apply {
            fitsSystemWindows = true
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.setSupportZoom(false)            // העיצוב מותאם למסך — בלי צביטה
            settings.userAgentString = settings.userAgentString + " FilterPhoneAdmin/1"
            addJavascriptInterface(Bridge(this@AdminDashboardActivity), "FPApp")
            webViewClient = Client()
            webChromeClient = Chrome()
        }
        setContentView(web)
        if (Build.VERSION.SDK_INT >= 33) {
            onBackInvokedDispatcher.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT) { back() }
        }
        if (savedInstanceState != null && web.restoreState(savedInstanceState) != null) return
        web.loadUrl("$base/admin/?app=1")
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

    // אנדרואיד 12 ומטה; ב-13+ — onBackInvokedDispatcher למעלה (כמו בסייר המוגן)
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

    private fun ours(uri: Uri?): Boolean = uri != null && uri.scheme == "https" && uri.host == host

    private inner class Client : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            if (ours(request.url)) return false
            // קישור החוצה (למשל דף של אפליקציה ב-Play / וואטסאפ של לקוח) — לא בתוך הדשבורד
            runCatching {
                if (request.url.scheme == "https" || request.url.scheme == "http") {
                    BrowserActivity.open(this@AdminDashboardActivity, request.url.toString(), null)
                } else {
                    startActivity(Intent(Intent.ACTION_VIEW, request.url))
                }
            }
            return true
        }

        override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
            if (!ours(url?.let(Uri::parse)) && url != "about:blank" && url?.startsWith("data:") != true) view.stopLoading()
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            Log.w(TAG, "dashboard load failed: ${error.errorCode} ${error.description}")
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

    /** "אין רשת" — בעברית, עם כפתור שטוען שוב את הדשבורד. */
    private fun offlinePage(): String = """
        <!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light dark">
        <style>body{font:17px/1.6 system-ui,sans-serif;margin:0;min-height:100vh;display:grid;place-items:center;
        text-align:center;padding:24px;box-sizing:border-box}button{font:inherit;font-weight:600;padding:14px 28px;
        border-radius:12px;border:0;background:#1f6feb;color:#fff;margin-top:18px;min-width:60%}</style></head>
        <body><div><h2>${getString(R.string.admin_offline_title)}</h2><p>${getString(R.string.admin_offline_text)}</p>
        <button onclick="location.href='${base}/admin/?app=1'">${getString(R.string.admin_offline_retry)}</button>
        </div></body></html>
    """.trimIndent()

    /**
     * הגשר לדשבורד: שמירת הכניסה מוצפנת בטלפון (במקום sessionStorage שנמחק בכל סגירה). רק הדשבורד שלנו
     * נטען בחלון הזה (Client), כך שרק הוא יכול לקרוא לכאן.
     */
    private class Bridge(context: Context) {
        private val store = SecureStore(context.applicationContext)

        @JavascriptInterface
        fun token(): String = store.get(K_TOKEN).orEmpty()

        @JavascriptInterface
        fun saveToken(token: String?) {
            store.put(K_TOKEN, token?.takeIf { it.length in 16..256 && it.all { c -> c.isLetterOrDigit() || c in "-_." } })
        }

        @JavascriptInterface
        fun clearToken() = store.put(K_TOKEN, null)
    }

    companion object {
        private const val TAG = "FilterPhone"
        private const val REQ_FILE = 7401
        private const val K_TOKEN = "admin_dashboard_token"
        private const val DEFAULT_BASE = "https://api.filterphone.com"
        private const val LAUNCHER = "com.filterphone.app.AdminLauncher"

        /** הסמל "FilterPhone ניהול" במסך הבית — רק במכשיר המנהל (השרת קובע). */
        fun setLauncher(context: Context, enabled: Boolean) {
            val pm = context.packageManager
            val cn = ComponentName(context.packageName, LAUNCHER)
            val want = if (enabled) PackageManager.COMPONENT_ENABLED_STATE_ENABLED else PackageManager.COMPONENT_ENABLED_STATE_DISABLED
            if (runCatching { pm.getComponentEnabledSetting(cn) }.getOrNull() == want) return
            runCatching { pm.setComponentEnabledSetting(cn, want, PackageManager.DONT_KILL_APP) }
                .onFailure { Log.w(TAG, "admin launcher toggle failed", it) }
        }

        fun open(context: Context) {
            context.startActivity(Intent(context, AdminDashboardActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }
}
