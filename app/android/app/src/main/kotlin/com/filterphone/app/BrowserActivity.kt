package com.filterphone.app

import android.annotation.SuppressLint
import android.app.Activity
import android.app.DownloadManager
import android.app.role.RoleManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.net.http.SslCertificate
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import android.util.Log
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.window.OnBackInvokedDispatcher
import android.webkit.CookieManager
import android.webkit.SslErrorHandler
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import java.io.ByteArrayInputStream
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate

/**
 * הסייר המוגן: דפדפן בתוך FilterPhone. כל התעבורה שלו עוברת ב-VPN ובסינון (תמונות, אתרים, מילים) —
 * בדיוק כמו Chrome. הוא סומך על תעודת הסינון של FilterPhone *בלבד* מעבר לתעודות של המערכת (לא על
 * תעודות משתמש אחרות), כך שהוא עובד גם בלי להוסיף אמון כללי באפליקציה.
 *
 * מצבים (SafeSites.mode, מהמדיניות): full — גלישה + חיפוש; essential — רק האתרים החיוניים; off — לא נפתח.
 * אפשר לקבוע אותו כדפדפן ברירת המחדל: קישורים מאפליקציות אחרות נפתחים כאן.
 */
class BrowserActivity : Activity() {
    private lateinit var web: WebView
    private lateinit var address: EditText
    private lateinit var progress: ProgressBar
    private lateinit var lock: ImageView
    private lateinit var notice: TextView
    private var mode = SafeSites.FULL
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private val caCert: X509Certificate? by lazy { loadCa(this) }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        mode = currentMode(this)
        if (mode == SafeSites.OFF) {
            Toast.makeText(this, R.string.browser_off, Toast.LENGTH_LONG).show()
            finish()
            return
        }
        setContentView(R.layout.activity_browser)
        web = findViewById(R.id.browser_web)
        address = findViewById(R.id.browser_address)
        progress = findViewById(R.id.browser_progress)
        lock = findViewById(R.id.browser_lock)
        notice = findViewById(R.id.browser_notice)
        address.hint = getString(if (mode == SafeSites.FULL) R.string.browser_hint_full else R.string.browser_hint_essential)

        findViewById<ImageButton>(R.id.browser_home).setOnClickListener { goHome() }
        findViewById<ImageButton>(R.id.browser_reload).setOnClickListener { web.reload() }
        address.setOnEditorActionListener { _, action, event ->
            if (action == EditorInfo.IME_ACTION_GO || event?.keyCode == KeyEvent.KEYCODE_ENTER) {
                go(address.text.toString())
                true
            } else {
                false
            }
        }

        web.settings.apply {
            javaScriptEnabled = true                  // אתרי בנקים / ממשלה לא עובדים בלי
            domStorageEnabled = true
            setSupportMultipleWindows(false)          // target=_blank נפתח באותו חלון (ונבדק כמו כל ניווט)
            javaScriptCanOpenWindowsAutomatically = false
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setGeolocationEnabled(false)
            builtInZoomControls = true
            displayZoomControls = false
            safeBrowsingEnabled = true
        }
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true)   // כניסה לבנקים / gov.il
        web.webViewClient = Client()
        web.webChromeClient = Chrome()
        web.setDownloadListener { url, userAgent, disposition, mime, _ -> download(url, userAgent, disposition, mime) }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            // אנדרואיד 13+: מחוות "אחורה" לא מגיעה ל-onBackPressed — אחורה בדף, ואז יציאה מהסייר
            onBackInvokedDispatcher.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT) { back() }
        }
        if (savedInstanceState != null && web.restoreState(savedInstanceState) != null) return
        if (!openFromIntent(intent)) goHome()
    }

    override fun onResume() {
        super.onResume()
        if (!::web.isInitialized) return
        mode = currentMode(this)                         // המדיניות השתנתה בזמן שהסייר היה פתוח
        if (mode == SafeSites.OFF) {
            Toast.makeText(this, R.string.browser_off, Toast.LENGTH_LONG).show()
            finish()
        } else if (!SafeSites.allows(mode, web.url?.let { SafeSites.hostOf(it) } ?: return)) {
            goHome()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        openFromIntent(intent)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (::web.isInitialized) web.saveState(outState)
    }

    override fun onDestroy() {
        if (::web.isInitialized) {
            web.stopLoading()
            web.destroy()
        }
        fileCallback?.onReceiveValue(null)
        fileCallback = null
        super.onDestroy()
    }

    /** עד אנדרואיד 12 (מגרסה 13 — הקריאה החוזרת שנרשמה ב-onCreate). */
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

    /** קישור מאפליקציה אחרת (דפדפן ברירת מחדל) / מהטאב "סייר מוגן". false — אין מה לפתוח. */
    private fun openFromIntent(intent: Intent?): Boolean {
        val url = intent?.dataString?.takeIf { it.startsWith("http://") || it.startsWith("https://") }
            ?: intent?.getStringExtra(EXTRA_QUERY)?.let { SafeSites.toUrl(it, mode) }
            ?: return false
        open(url)
        return true
    }

    private fun go(input: String) {
        val url = SafeSites.toUrl(input, mode)
        if (url == null) {
            showNotice(getString(R.string.browser_search_off))
            return
        }
        hideKeyboard()
        open(url)
    }

    private fun open(url: String) {
        if (!SafeSites.allows(mode, SafeSites.hostOf(url))) {
            showNotice(getString(R.string.browser_not_allowed))
            return
        }
        notice.visibility = View.GONE
        web.loadUrl(url)
    }

    /** חזרה לטאב "סייר מוגן" באפליקציה (האתרים החיוניים והחיפוש). */
    private fun goHome() {
        runCatching {
            startActivity(Intent(this, MainActivity::class.java)
                .putExtra(SupportNotify.EXTRA_OPEN, "browser")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP))
        }
        finish()
    }

    private fun showNotice(text: String) {
        notice.text = text
        notice.visibility = View.VISIBLE
    }

    private fun hideKeyboard() {
        address.clearFocus()
        getSystemService(InputMethodManager::class.java)?.hideSoftInputFromWindow(address.windowToken, 0)
    }

    private fun download(url: String, userAgent: String?, disposition: String?, mime: String?) {
        if (!SafeSites.allows(mode, SafeSites.hostOf(url))) return
        runCatching {
            val name = URLUtil.guessFileName(url, disposition, mime)
            val req = DownloadManager.Request(Uri.parse(url))
                .setMimeType(mime)
                .addRequestHeader("User-Agent", userAgent ?: "")
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            // לתיקיית ההורדות (באנדרואיד 10+ בלי הרשאה; בישנים — המיקום של מנהל ההורדות)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) req.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
            CookieManager.getInstance().getCookie(url)?.let { req.addRequestHeader("Cookie", it) }
            getSystemService(DownloadManager::class.java)?.enqueue(req)
            Toast.makeText(this, getString(R.string.browser_downloading, name), Toast.LENGTH_SHORT).show()
        }.onFailure { Log.w(TAG, "download failed", it) }
    }

    private inner class Client : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val uri = request.url
            val scheme = uri.scheme?.lowercase()
            if (scheme == "http" || scheme == "https") {
                // אתרים חיוניים בלבד: ניווט של הדף הראשי לאתר אחר — לא (רכיבים בתוך הדף — כן)
                if (request.isForMainFrame && !SafeSites.allows(mode, uri.host)) {
                    showNotice(getString(R.string.browser_not_allowed))
                    return true
                }
                return false
            }
            if (SafeSites.isExternalScheme(scheme)) {
                runCatching { startActivity(Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            }
            return true                                   // intent:, market:, javascript: וכו' — לא יוצאים מהסייר
        }

        override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
            if (url != null && !SafeSites.allows(mode, SafeSites.hostOf(url)) && url.startsWith("http")) {
                view.stopLoading()                        // הפניה מהשרת לאתר שלא ברשימה
                showNotice(getString(R.string.browser_not_allowed))
                return
            }
            notice.visibility = View.GONE
            if (url != null && !address.hasFocus()) address.setText(url)
            lock.visibility = if (url?.startsWith("https://") == true) View.VISIBLE else View.INVISIBLE
            progress.visibility = View.VISIBLE
        }

        override fun onPageFinished(view: WebView, url: String?) {
            progress.visibility = View.GONE
            if (url != null && !address.hasFocus() && url.startsWith("http")) address.setText(url)
        }

        /**
         * תעודה שהמערכת לא מכירה: ממשיכים רק אם היא חתומה בתעודת הסינון של FilterPhone (הסינון באמצע
         * הדרך) ותקינה בכל השאר (שם האתר, תוקף). כל תעודה אחרת — נחסם, כמו בכל דפדפן.
         */
        @SuppressLint("WebViewClientOnReceivedSslError")
        override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
            if (signedByFilter(error)) handler.proceed() else {
                handler.cancel()
                if (error.url == view.url || view.url == null) showNotice(getString(R.string.browser_ssl_error))
            }
        }
    }

    private inner class Chrome : WebChromeClient() {
        override fun onProgressChanged(view: WebView, newProgress: Int) {
            progress.progress = newProgress
        }

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

    private fun signedByFilter(error: SslError): Boolean {
        val bad = listOf(SslError.SSL_NOTYETVALID, SslError.SSL_EXPIRED, SslError.SSL_IDMISMATCH,
            SslError.SSL_DATE_INVALID, SslError.SSL_INVALID)
        if (!error.hasError(SslError.SSL_UNTRUSTED) || bad.any { error.hasError(it) }) return false
        val ca = caCert ?: return false
        return runCatching {
            val leaf = x509(error.certificate) ?: return false
            leaf.checkValidity()
            leaf.issuerX500Principal == ca.subjectX500Principal && run { leaf.verify(ca.publicKey); true }
        }.getOrDefault(false)
    }

    companion object {
        private const val TAG = "FilterPhone"
        private const val REQ_FILE = 41
        const val EXTRA_QUERY = "com.filterphone.app.extra.QUERY"

        /** המצב עכשיו: מה שהשרת קבע, ו-Chrome חסום בלי הוראה מהשרת — כבוי. */
        fun currentMode(context: Context): String {
            val policy = GuardPolicy.get(context)
            val chromeBlocked = GuardPolicy.check(context, policy, WaBrowser.CHROME, null) != null
            return SafeSites.mode(policy.safeBrowser, chromeBlocked)
        }

        /** פתיחה מהטאב: קישור לאתר, או מה שהוקלד בחיפוש. */
        fun open(context: Context, url: String?, query: String?) {
            val intent = Intent(context, BrowserActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (url != null) intent.data = Uri.parse(url) else if (query != null) intent.putExtra(EXTRA_QUERY, query)
            context.startActivity(intent)
        }

        /** FilterPhone היא דפדפן ברירת המחדל עכשיו. */
        fun isDefault(context: Context): Boolean = runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                context.getSystemService(RoleManager::class.java)?.isRoleHeld(RoleManager.ROLE_BROWSER) == true
            } else {
                val probe = Intent(Intent.ACTION_VIEW, Uri.parse("https://example.com"))
                context.packageManager.resolveActivity(probe, 0)?.activityInfo?.packageName == context.packageName
            }
        }.getOrDefault(false)

        /** בקשה מהמערכת להפוך לדפדפן ברירת המחדל (חלון אישור של אנדרואיד; בישנים — מסך ההגדרות). */
        fun requestDefault(activity: Activity, requestCode: Int): Boolean = runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val rm = activity.getSystemService(RoleManager::class.java)
                if (rm != null && rm.isRoleAvailable(RoleManager.ROLE_BROWSER) && !rm.isRoleHeld(RoleManager.ROLE_BROWSER)) {
                    @Suppress("DEPRECATION")
                    activity.startActivityForResult(rm.createRequestRoleIntent(RoleManager.ROLE_BROWSER), requestCode)
                    return true
                }
            }
            activity.startActivity(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS))
            true
        }.getOrDefault(false)

        private fun loadCa(context: Context): X509Certificate? = runCatching {
            val pem = ProtectionState.string(context, ProtectionState.CA_PEM) ?: return null
            CertificateFactory.getInstance("X.509")
                .generateCertificate(ByteArrayInputStream(CertHelper.pemToDer(pem))) as X509Certificate
        }.getOrNull()

        /** התעודה של האתר מתוך השגיאה (SslCertificate שומר אותה כ-DER גם בגרסאות ישנות). */
        private fun x509(cert: SslCertificate?): X509Certificate? {
            cert ?: return null
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) cert.x509Certificate?.let { return it }
            val der = SslCertificate.saveState(cert)?.getByteArray("x509-certificate") ?: return null
            return CertificateFactory.getInstance("X.509").generateCertificate(ByteArrayInputStream(der)) as X509Certificate
        }
    }
}
