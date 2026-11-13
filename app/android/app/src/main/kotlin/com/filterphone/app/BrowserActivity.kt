package com.filterphone.app

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.app.DownloadManager
import android.app.role.RoleManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.ShortcutInfo
import android.content.pm.ShortcutManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.Icon
import android.net.Uri
import android.net.http.SslCertificate
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.Editable
import android.text.TextUtils
import android.text.TextWatcher
import android.text.format.DateUtils
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.webkit.CookieManager
import android.webkit.SslErrorHandler
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import android.window.OnBackInvokedDispatcher
import com.wireguard.android.backend.Tunnel
import java.io.ByteArrayInputStream
import java.io.File
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate

/**
 * הסייר המוגן: דפדפן שהוא חלק מ-FilterPhone (אותה התקנה), עם סמל משלו במסך הבית ובאחרונים. כל התעבורה שלו
 * עוברת ב-VPN ובסינון (תמונות, אתרים, מילים) — כמו Chrome; והוא נעול כשההגנה לא פעילה (BrowserGate):
 * בלי FilterPhone מופעל, בהשהיה, או כשהסינון כבוי — אין גלישה. הוא סומך על תעודת הסינון של FilterPhone
 * *בלבד* מעבר לתעודות של המערכת (לא על תעודות משתמש אחרות).
 *
 * מצבים (SafeSites.mode, מהמדיניות): full — גלישה + חיפוש; essential — רק האתרים המאושרים, והחיפוש מחפש בתוכם;
 * off — נעול. דף בית משלו (חיפוש, האתרים המוצמדים, כל האתרים המאושרים), כרטיסיות, היסטוריה, הוספת אתר למסך
 * הבית. מה שהוא זוכר (BrowserData) נשאר בטלפון בלבד. אפשר לקבוע אותו כדפדפן ברירת המחדל.
 */
class BrowserActivity : Activity() {
    private lateinit var address: EditText
    private lateinit var progress: ProgressBar
    private lateinit var lock: ImageView
    private lateinit var notice: TextView
    private lateinit var tabsButton: TextView
    private lateinit var pages: FrameLayout
    private lateinit var homeView: ScrollView
    private lateinit var gateView: ScrollView
    private lateinit var data: BrowserData
    private var mode = SafeSites.FULL
    private var blocked: BrowserGate.Block? = null
    private var allSitesOpen = false
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private val caCert: X509Certificate? by lazy { loadCa(this) }
    private val main = Handler(Looper.getMainLooper())
    private val tabs = ArrayList<Tab>()
    private var current = 0
    private val tab: Tab get() = tabs[current]

    /** החיבור נפל / חזר: בודקים שוב אחרי רגע (חיבור מחדש קצר לא נועל את הסייר באמצע הגלישה). */
    private val recheckGate = Runnable { refreshGate() }
    private val tunnelListener: (Tunnel.State) -> Unit = { state ->
        main.removeCallbacks(recheckGate)
        if (state == Tunnel.State.UP) main.post(recheckGate) else main.postDelayed(recheckGate, GATE_GRACE_MS)
    }

    /** כרטיסייה: WebView משלה (נוצר כשצריך), והאם היא מציגה עכשיו את דף הבית. */
    private class Tab {
        var web: WebView? = null
        var url: String? = null
        var title: String = ""
        var icon: Bitmap? = null
        var home = true
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        data = BrowserData(this)
        setContentView(R.layout.activity_browser)
        address = findViewById(R.id.browser_address)
        progress = findViewById(R.id.browser_progress)
        lock = findViewById(R.id.browser_lock)
        notice = findViewById(R.id.browser_notice)
        tabsButton = findViewById(R.id.browser_tabs)
        pages = findViewById(R.id.browser_pages)
        homeView = findViewById(R.id.browser_home_view)
        gateView = findViewById(R.id.browser_gate)

        findViewById<ImageButton>(R.id.browser_home).setOnClickListener { goHome() }
        findViewById<ImageButton>(R.id.browser_menu).setOnClickListener { showMenu(it) }
        tabsButton.setOnClickListener { showTabs() }
        address.setOnEditorActionListener { _, action, event ->
            if (action == EditorInfo.IME_ACTION_GO || event?.keyCode == KeyEvent.KEYCODE_ENTER) {
                go(address.text.toString())
                true
            } else {
                false
            }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            // אנדרואיד 13+: מחוות "אחורה" לא מגיעה ל-onBackPressed
            onBackInvokedDispatcher.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT) { back() }
        }
        if (!TunnelManager.isInitialized) TunnelManager.init(applicationContext)
        TunnelManager.addListener(tunnelListener)

        mode = currentMode(this)
        val (saved, cur) = data.tabs()
        saved.forEach { p -> tabs += Tab().apply { url = p.url; title = p.title; home = false } }
        if (tabs.isEmpty()) tabs += Tab()
        current = cur.coerceIn(0, tabs.size - 1)
        refreshGate()                                    // גם מציג את הכרטיסייה הנוכחית
        if (blocked == null) openFromIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        refreshGate()                                    // ההגנה / המדיניות השתנו בזמן שהסייר היה ברקע
    }

    override fun onPause() {
        super.onPause()
        saveTabs()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        refreshGate()
        if (blocked == null) openFromIntent(intent)
    }

    override fun onDestroy() {
        TunnelManager.removeListener(tunnelListener)
        main.removeCallbacksAndMessages(null)
        tabs.forEach { t -> t.web?.let { it.stopLoading(); it.destroy() } }
        fileCallback?.onReceiveValue(null)
        fileCallback = null
        super.onDestroy()
    }

    /** עד אנדרואיד 12 (מגרסה 13 — הקריאה החוזרת שנרשמה ב-onCreate). */
    @SuppressLint("GestureBackNavigation")
    @Deprecated("Deprecated in Java")
    override fun onBackPressed() = back()

    /** אחורה: בדף — לדף הקודם; מהדף הראשון — לדף הבית; מדף הבית — לדף שהיה פתוח, או החוצה (נשאר באחרונים). */
    private fun back() {
        if (tabs.isEmpty() || blocked != null) {
            moveTaskToBack(true)
            return
        }
        val t = tab
        val w = t.web
        when {
            t.home && w != null && t.url != null -> { t.home = false; showTab(current) }
            !t.home && w != null && w.canGoBack() -> w.goBack()
            !t.home -> { t.home = true; showTab(current) }
            else -> moveTaskToBack(true)
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_FILE) return
        fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data))
        fileCallback = null
    }

    // ------------------------------------------------------------ נעילה כשאין הגנה פעילה

    private fun refreshGate() {
        mode = currentMode(this)
        val b = BrowserGate.check(this)
        val was = blocked
        blocked = b
        if (b != null) {
            tabs.forEach { t -> t.web?.let { it.stopLoading(); it.onPause() } }
            pages.visibility = View.GONE
            homeView.visibility = View.GONE
            notice.visibility = View.GONE
            progress.visibility = View.GONE
            address.isEnabled = false
            address.setText("")
            showGate(b)
            return
        }
        gateView.visibility = View.GONE
        pages.visibility = View.VISIBLE
        address.isEnabled = true
        if (was != null) tabs.forEach { t -> t.web?.onResume() }
        // המצב השתנה (למשל מגלישה מלאה לאתרים מאושרים בלבד) — דף שכבר לא מותר נסגר לדף הבית
        tabs.forEach { t ->
            if (!t.home && t.url != null && !SafeSites.allowsUrl(mode, t.url)) {
                t.web?.let { w -> w.stopLoading(); pages.removeView(w); w.destroy() }
                t.web = null
                t.url = null
                t.home = true
            }
        }
        if (tabs.isNotEmpty()) showTab(current)
    }

    private fun showGate(b: BrowserGate.Block) {
        val col = column(Gravity.CENTER_HORIZONTAL).apply { setPadding(dp(28), dp(56), dp(28), dp(28)) }
        col.addView(ImageView(this).apply { setImageResource(R.mipmap.ic_browser) }, LinearLayout.LayoutParams(dp(76), dp(76)))
        col.addView(text(getString(if (b == BrowserGate.Block.OFF) R.string.browser_gate_off_title else R.string.browser_gate_title),
            20f, bold = true, center = true), margins(top = 18))
        val reason = when (b) {
            BrowserGate.Block.OFF -> R.string.browser_gate_off
            BrowserGate.Block.NOT_SET_UP -> R.string.browser_gate_not_set_up
            BrowserGate.Block.PAUSED -> R.string.browser_gate_paused
            BrowserGate.Block.FILTER_OFF -> R.string.browser_gate_filter_off
        }
        col.addView(text(getString(reason), 15f, sub = true, center = true), margins(top = 10))
        col.addView(Button(this).apply {
            text = getString(R.string.browser_open_app)
            setOnClickListener { openApp() }
        }, margins(top = 24).apply { width = ViewGroup.LayoutParams.WRAP_CONTENT })
        gateView.removeAllViews()
        gateView.addView(col)
        gateView.visibility = View.VISIBLE
    }

    // ------------------------------------------------------------ כרטיסיות

    private fun showTab(i: Int) {
        current = i.coerceIn(0, tabs.size - 1)
        val t = tab
        if (!t.home && t.web == null) {
            val url = t.url
            if (url != null && SafeSites.allowsUrl(mode, url)) {
                t.web = createWeb(t).also { it.loadUrl(url) }
            } else {
                t.home = true
            }
        }
        tabs.forEachIndexed { j, other -> other.web?.visibility = if (j == current && !other.home) View.VISIBLE else View.GONE }
        homeView.visibility = if (t.home) View.VISIBLE else View.GONE
        if (t.home) {
            buildHome()
            address.setText("")
            lock.visibility = View.GONE
            progress.visibility = View.GONE
        } else {
            val url = t.web?.url ?: t.url
            if (!address.hasFocus()) address.setText(url)
            lock.visibility = if (url?.startsWith("https://") == true) View.VISIBLE else View.GONE
        }
        address.hint = getString(if (mode == SafeSites.FULL) R.string.browser_hint_full else R.string.browser_search_hint_essential)
        tabsButton.text = tabs.size.toString()
    }

    private fun newTab(): Boolean {
        if (tabs.size >= BrowserData.MAX_TABS) {
            Toast.makeText(this, getString(R.string.browser_tabs_max, BrowserData.MAX_TABS), Toast.LENGTH_SHORT).show()
            return false
        }
        tabs += Tab()
        showTab(tabs.size - 1)
        return true
    }

    private fun closeTab(i: Int) {
        val t = tabs.removeAt(i)
        t.web?.let { pages.removeView(it); it.stopLoading(); it.destroy() }
        if (tabs.isEmpty()) tabs += Tab()
        showTab(if (current >= tabs.size) tabs.size - 1 else if (i < current) current - 1 else current)
    }

    private fun saveTabs() {
        if (!::data.isInitialized) return
        val open = tabs.filter { !it.home && it.url != null }
        val cur = open.indexOf(tabs.getOrNull(current)).coerceAtLeast(0)
        data.saveTabs(open.map { BrowserData.Page(it.title, it.url ?: "") }, cur)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun createWeb(t: Tab): WebView {
        val web = WebView(this)
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
        web.webViewClient = Client(t)
        web.webChromeClient = Chrome(t)
        web.setDownloadListener { url, userAgent, disposition, mime, _ -> download(url, userAgent, disposition, mime) }
        pages.addView(web, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        return web
    }

    private fun showTabs() {
        if (blocked != null) return
        val list = column()
        var dialog: AlertDialog? = null
        tabs.forEachIndexed { i, t ->
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(16), dp(10), dp(6), dp(10))
                if (i == current) background = rounded(getColor(R.color.browser_tile), dp(12).toFloat())
            }
            val label = column()
            val title = if (t.home) getString(R.string.browser_home_tab) else t.title.ifBlank { SafeSites.hostOf(t.url ?: "") ?: "" }
            label.addView(text(title, 15f, bold = true, lines = 1))
            if (!t.home) label.addView(text(SafeSites.hostOf(t.url ?: "") ?: "", 13f, sub = true, lines = 1))
            row.addView(label, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            row.addView(ImageButton(this).apply {
                setImageResource(android.R.drawable.ic_menu_close_clear_cancel)
                background = null
                contentDescription = getString(R.string.browser_close_tab)
                setOnClickListener { dialog?.dismiss(); closeTab(i); showTabs() }
            }, LinearLayout.LayoutParams(dp(44), dp(44)))
            row.setOnClickListener { dialog?.dismiss(); showTab(i) }
            list.addView(row, margins(bottom = 4))
        }
        dialog = AlertDialog.Builder(this)
            .setTitle(R.string.browser_tabs)
            .setView(ScrollView(this).apply { setPadding(dp(12), dp(4), dp(12), 0); addView(list) })
            .setPositiveButton(R.string.browser_new_tab) { _, _ -> newTab() }
            .setNegativeButton(R.string.browser_close, null)
            .show()
    }

    // ------------------------------------------------------------ דף הבית

    private fun goHome() {
        if (blocked != null || tabs.isEmpty()) return
        tab.home = true
        notice.visibility = View.GONE
        hideKeyboard()
        showTab(current)
    }

    private fun buildHome() {
        val col = column().apply { setPadding(dp(18), dp(22), dp(18), dp(28)) }
        // כותרת: הסמל של הסייר, שם, ומה מותר כאן
        val head = column(Gravity.CENTER_HORIZONTAL)
        head.addView(ImageView(this).apply { setImageResource(R.mipmap.ic_browser) }, LinearLayout.LayoutParams(dp(58), dp(58)))
        head.addView(text(getString(R.string.browser_label), 22f, bold = true, center = true), margins(top = 8))
        head.addView(text(getString(if (mode == SafeSites.FULL) R.string.browser_home_subtitle_full
            else R.string.browser_home_subtitle_essential), 13f, sub = true, center = true), margins(top = 2))
        col.addView(head)

        // חיפוש (באתרים מאושרים — חיפוש בתוכם) + הצעות מהרשימה בזמן ההקלדה
        val search = EditText(this).apply {
            background = getDrawable(R.drawable.browser_address_bg)
            hint = getString(if (mode == SafeSites.FULL) R.string.browser_hint_full else R.string.browser_search_hint_essential)
            setSingleLine(true)
            imeOptions = EditorInfo.IME_ACTION_SEARCH
            importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO
            textAlignment = View.TEXT_ALIGNMENT_VIEW_START
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            setPadding(dp(16), 0, dp(16), 0)
            compoundDrawablePadding = dp(8)
            setCompoundDrawablesRelativeWithIntrinsicBounds(R.drawable.ic_browser_search, 0, 0, 0)
            compoundDrawableTintList = android.content.res.ColorStateList.valueOf(getColor(R.color.browser_text_sub))
        }
        val suggestions = column()
        search.setOnEditorActionListener { v, action, event ->
            if (action == EditorInfo.IME_ACTION_SEARCH || event?.keyCode == KeyEvent.KEYCODE_ENTER) {
                go(v.text.toString())
                true
            } else {
                false
            }
        }
        search.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
            override fun afterTextChanged(s: Editable?) = fillSuggestions(suggestions, s?.toString().orEmpty())
        })
        col.addView(search, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(50)).apply { topMargin = dp(20) })
        col.addView(suggestions, margins(top = 6))

        // האתרים המוצמדים — כמו קיצורי הדרך בדף הבית של כרום
        val pins = data.pins().filter { SafeSites.allowsUrl(mode, it.url) }
        if (pins.isNotEmpty()) {
            col.addView(text(getString(R.string.browser_pins), 15f, bold = true), margins(top = 22, bottom = 10))
            pins.chunked(4).forEach { row ->
                val line = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
                row.forEach { p -> line.addView(tile(p), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)) }
                repeat(4 - row.size) { line.addView(View(this), LinearLayout.LayoutParams(0, 1, 1f)) }
                col.addView(line, margins(bottom = 12))
            }
        }

        // כל האתרים המאושרים — נפתח בלחיצה (במקום שבו בכרום מופיעות הכתבות)
        val allTitle = getString(if (mode == SafeSites.FULL) R.string.browser_all_sites_full else R.string.browser_all_sites_essential)
        val list = column().apply { visibility = if (allSitesOpen) View.VISIBLE else View.GONE }
        val header = text("$allTitle  ${if (allSitesOpen) "▴" else "▾"}", 15f, bold = true).apply {
            setPadding(dp(14), dp(12), dp(14), dp(12))
            background = rounded(getColor(R.color.browser_card), dp(14).toFloat())
        }
        header.setOnClickListener {
            allSitesOpen = !allSitesOpen
            list.visibility = if (allSitesOpen) View.VISIBLE else View.GONE
            header.text = "$allTitle  ${if (allSitesOpen) "▴" else "▾"}"
        }
        col.addView(header, margins(top = 10))
        SafeSites.SITES.groupBy { it.group }.forEach { (group, sites) ->
            list.addView(text(group, 13f, bold = true, accent = true), margins(top = 14, bottom = 4))
            sites.forEach { s -> list.addView(siteRow(BrowserData.Page(s.title, s.url))) }
        }
        col.addView(list)

        homeView.removeAllViews()
        homeView.addView(col)
    }

    private fun fillSuggestions(box: LinearLayout, query: String) {
        box.removeAllViews()
        val q = query.trim()
        if (q.isEmpty()) return
        SafeSites.suggest(q).forEach { s -> box.addView(siteRow(BrowserData.Page(s.title, s.url))) }
        if (SafeSites.toUrl(q, mode) != null) {
            box.addView(text(getString(R.string.browser_search_for, q), 15f, accent = true, lines = 1).apply {
                setPadding(dp(14), dp(12), dp(14), dp(12))
                setOnClickListener { go(q) }
            })
        }
    }

    /** קיצור דרך עגול בדף הבית: הסמל של האתר (אם נשמר) או האות הראשונה, והשם. */
    private fun tile(p: BrowserData.Page): View {
        val box = column(Gravity.CENTER_HORIZONTAL).apply { setPadding(dp(2), dp(4), dp(2), dp(4)) }
        box.addView(badge(p, 52), LinearLayout.LayoutParams(dp(52), dp(52)))
        box.addView(text(p.title.ifBlank { SafeSites.hostOf(p.url) ?: "" }, 12f, center = true, lines = 2), margins(top = 6))
        box.setOnClickListener { open(p.url) }
        box.setOnLongClickListener { pinMenu(it, p); true }
        return box
    }

    private fun siteRow(p: BrowserData.Page): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(10), dp(8), dp(10), dp(8))
        }
        row.addView(badge(p, 36), LinearLayout.LayoutParams(dp(36), dp(36)))
        val label = column().apply { setPadding(dp(12), 0, 0, 0) }
        label.addView(text(p.title, 15f, lines = 1))
        label.addView(text(SafeSites.hostOf(p.url) ?: "", 12f, sub = true, lines = 1))
        row.addView(label, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        row.setOnClickListener { open(p.url) }
        row.setOnLongClickListener { pinMenu(it, p); true }
        return row
    }

    private fun badge(p: BrowserData.Page, sizeDp: Int): View {
        val frame = FrameLayout(this).apply { background = oval(getColor(R.color.browser_tile)) }
        val icon = IconCache.load(this, SafeSites.hostOf(p.url))
        if (icon != null) {
            frame.addView(ImageView(this).apply { setImageBitmap(icon) },
                FrameLayout.LayoutParams(dp(sizeDp / 2), dp(sizeDp / 2), Gravity.CENTER))
        } else {
            frame.addView(text(letter(p), (sizeDp * 0.38f), bold = true, accent = true, center = true).apply {
                gravity = Gravity.CENTER
            }, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        }
        return frame
    }

    private fun letter(p: BrowserData.Page): String =
        (p.title.trim().firstOrNull() ?: SafeSites.hostOf(p.url)?.removePrefix("www.")?.firstOrNull() ?: '•').toString().uppercase()

    private fun pinMenu(anchor: View, p: BrowserData.Page) {
        val menu = PopupMenu(this, anchor)
        val pinned = data.isPinned(p.url)
        menu.menu.add(0, 1, 0, if (pinned) R.string.browser_unpin else R.string.browser_pin)
        menu.menu.add(0, 2, 1, R.string.browser_add_to_home)
        menu.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                1 -> { if (pinned) data.unpin(p.url) else data.pin(p); if (tab.home) buildHome() }
                2 -> addShortcut(p.title, p.url, IconCache.load(this, SafeSites.hostOf(p.url)))
            }
            true
        }
        menu.show()
    }

    // ------------------------------------------------------------ תפריט, היסטוריה, קיצורי דרך

    private fun showMenu(anchor: View) {
        if (blocked != null) {
            val menu = PopupMenu(this, anchor)
            menu.menu.add(0, 7, 0, R.string.browser_open_app)
            menu.setOnMenuItemClickListener { openApp(); true }
            menu.show()
            return
        }
        val onPage = !tab.home && tab.url != null
        val url = tab.web?.url ?: tab.url
        val menu = PopupMenu(this, anchor)
        menu.menu.add(0, 1, 0, R.string.browser_new_tab)
        menu.menu.add(0, 2, 1, R.string.browser_history)
        if (onPage && url != null) {
            menu.menu.add(0, 3, 2, R.string.browser_reload)
            menu.menu.add(0, 4, 3, if (data.isPinned(url)) R.string.browser_unpin else R.string.browser_pin)
            menu.menu.add(0, 5, 4, R.string.browser_add_to_home)
            menu.menu.add(0, 6, 5, R.string.browser_share)
        }
        menu.menu.add(0, 7, 6, R.string.browser_open_app)
        menu.setOnMenuItemClickListener { item ->
            val title = tab.title.ifBlank { SafeSites.hostOf(url ?: "") ?: "" }
            when (item.itemId) {
                1 -> newTab()
                2 -> showHistory()
                3 -> tab.web?.reload()
                4 -> url?.let { if (data.isPinned(it)) data.unpin(it) else data.pin(BrowserData.Page(title, it)) }
                5 -> url?.let { addShortcut(title, it, tab.icon) }
                6 -> url?.let { share(it) }
                7 -> openApp()
            }
            true
        }
        menu.show()
    }

    private fun showHistory() {
        val history = data.history()
        val list = column()
        var dialog: AlertDialog? = null
        if (history.isEmpty()) list.addView(text(getString(R.string.browser_history_empty), 15f, sub = true), margins(top = 8))
        history.forEach { p ->
            val row = column().apply { setPadding(dp(10), dp(9), dp(10), dp(9)) }
            row.addView(text(p.title.ifBlank { p.url }, 15f, lines = 1))
            val time = DateUtils.getRelativeTimeSpanString(p.at, System.currentTimeMillis(), DateUtils.MINUTE_IN_MILLIS)
            row.addView(text("${SafeSites.hostOf(p.url) ?: ""} · $time", 12f, sub = true, lines = 1))
            row.setOnClickListener { dialog?.dismiss(); open(p.url) }
            list.addView(row)
        }
        val builder = AlertDialog.Builder(this)
            .setTitle(R.string.browser_history)
            .setView(ScrollView(this).apply { setPadding(dp(14), dp(4), dp(14), 0); addView(list) })
            .setNegativeButton(R.string.browser_close, null)
        if (history.isNotEmpty()) {
            builder.setNeutralButton(R.string.browser_history_clear) { _, _ ->
                AlertDialog.Builder(this)
                    .setMessage(R.string.browser_history_clear_confirm)
                    .setPositiveButton(R.string.browser_delete) { _, _ -> data.clearHistory() }
                    .setNegativeButton(R.string.browser_cancel, null)
                    .show()
            }
        }
        dialog = builder.show()
    }

    /** קיצור דרך לאתר במסך הבית — נפתח ישר בסייר (והסייר בודק שההגנה פעילה ושהאתר מותר). */
    private fun addShortcut(title: String, url: String, icon: Bitmap?) {
        val sm = getSystemService(ShortcutManager::class.java)
        if (sm == null || !sm.isRequestPinShortcutSupported) {
            Toast.makeText(this, R.string.browser_shortcuts_unsupported, Toast.LENGTH_SHORT).show()
            return
        }
        val label = title.ifBlank { SafeSites.hostOf(url) ?: url }
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url), this, BrowserActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching {
            val info = ShortcutInfo.Builder(this, "site-" + Integer.toHexString(url.hashCode()))
                .setShortLabel(label.take(24))
                .setLongLabel(label.take(60))
                .setIcon(Icon.createWithAdaptiveBitmap(shortcutBitmap(label, icon)))
                .setIntent(intent)
                .build()
            sm.requestPinShortcut(info, null)
            Toast.makeText(this, R.string.browser_added_to_home, Toast.LENGTH_SHORT).show()
        }.onFailure { Log.w(TAG, "pin shortcut failed", it) }
    }

    /** סמל לקיצור הדרך: עיגול בצבעי הסייר, ובמרכזו הסמל של האתר או האות הראשונה (באזור הבטוח של סמל מסתגל). */
    private fun shortcutBitmap(label: String, icon: Bitmap?): Bitmap {
        val size = 432
        val bmp = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val c = Canvas(bmp)
        c.drawColor(Color.parseColor("#0B78BD"))
        val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
        if (icon != null) {
            paint.color = Color.WHITE
            c.drawCircle(size / 2f, size / 2f, size * 0.24f, paint)
            val r = size * 0.15f
            c.drawBitmap(icon, null, RectF(size / 2f - r, size / 2f - r, size / 2f + r, size / 2f + r), paint)
        } else {
            paint.color = Color.WHITE
            paint.textSize = size * 0.34f
            paint.typeface = Typeface.DEFAULT_BOLD
            paint.textAlign = Paint.Align.CENTER
            val ch = label.trim().firstOrNull()?.toString()?.uppercase() ?: "•"
            c.drawText(ch, size / 2f, size / 2f - (paint.descent() + paint.ascent()) / 2, paint)
        }
        return bmp
    }

    private fun share(url: String) {
        runCatching {
            startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, url), null))
        }
    }

    private fun openApp() {
        runCatching { startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
    }

    // ------------------------------------------------------------ ניווט

    /** קישור מאפליקציה אחרת (דפדפן ברירת מחדל), מקיצור דרך במסך הבית, או מהטאב "סייר מוגן". */
    private fun openFromIntent(intent: Intent?): Boolean {
        intent ?: return false
        val link = intent.dataString?.takeIf { it.startsWith("http://") || it.startsWith("https://") }
        val query = intent.getStringExtra(EXTRA_QUERY)
        if (link == null && query == null) return false
        // בכרטיסייה חדשה — אלא אם הנוכחית היא דף בית ריק
        if (!(tab.home && tab.web == null) && !newTab()) return true
        if (link != null) open(link) else go(query.orEmpty())
        intent.data = null
        intent.removeExtra(EXTRA_QUERY)
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
        if (blocked != null || BrowserGate.check(this) != null) {
            refreshGate()
            return
        }
        val target = SafeSites.restrictSearch(url, mode) ?: url
        if (!SafeSites.allowsUrl(mode, target)) {
            showNotice(getString(R.string.browser_not_allowed))
            return
        }
        notice.visibility = View.GONE
        val t = tab
        val web = t.web ?: createWeb(t).also { t.web = it }
        t.home = false
        t.url = target
        showTab(current)
        web.loadUrl(target)
    }

    private fun showNotice(text: String) {
        notice.text = text
        notice.visibility = View.VISIBLE
    }

    private fun hideKeyboard() {
        address.clearFocus()
        currentFocus?.clearFocus()
        getSystemService(InputMethodManager::class.java)?.hideSoftInputFromWindow(address.windowToken, 0)
    }

    private fun download(url: String, userAgent: String?, disposition: String?, mime: String?) {
        if (blocked != null || !SafeSites.allows(mode, SafeSites.hostOf(url))) return
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

    private inner class Client(private val t: Tab) : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val uri = request.url
            val scheme = uri.scheme?.lowercase()
            if (scheme == "http" || scheme == "https") {
                if (!request.isForMainFrame) return false              // רכיבים בתוך הדף — כן
                if (blocked != null) return true
                val url = uri.toString()
                // תוצאה של גוגל דרך /url?q= — בודקים את האתר האמיתי, ונכנסים אליו ישר
                SafeSites.unwrapRedirect(url)?.let { real ->
                    if (SafeSites.allowsUrl(mode, real)) view.loadUrl(real) else showNotice(getString(R.string.browser_not_allowed))
                    return true
                }
                // באתרים מאושרים בלבד: חיפוש חדש בדף התוצאות — נשאר בתוך האתרים המאושרים
                SafeSites.restrictSearch(url, mode)?.let { fixed ->
                    view.loadUrl(fixed)
                    return true
                }
                if (!SafeSites.allowsUrl(mode, url)) {
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
            if (blocked != null) {
                view.stopLoading()
                return
            }
            if (url != null && url.startsWith("http")) {
                val fixed = SafeSites.restrictSearch(url, mode)
                if (fixed != null) {
                    view.stopLoading()
                    view.loadUrl(fixed)
                    return
                }
                if (!SafeSites.allowsUrl(mode, url) && SafeSites.unwrapRedirect(url) == null) {
                    view.stopLoading()                    // הפניה מהשרת לאתר שלא ברשימה
                    showNotice(getString(R.string.browser_not_allowed))
                    return
                }
                t.url = url
            }
            if (t !== tabs.getOrNull(current)) return
            notice.visibility = View.GONE
            if (url != null && !address.hasFocus()) address.setText(url)
            lock.visibility = if (url?.startsWith("https://") == true) View.VISIBLE else View.GONE
            progress.visibility = View.VISIBLE
        }

        override fun onPageFinished(view: WebView, url: String?) {
            if (url == null || !url.startsWith("http")) return
            t.title = view.title.orEmpty()
            if (SafeSites.allowsUrl(mode, url)) data.visit(BrowserData.Page(t.title, url, System.currentTimeMillis()))
            if (t !== tabs.getOrNull(current)) return
            progress.visibility = View.GONE
            if (!address.hasFocus()) address.setText(url)
        }

        /**
         * תעודה שהמערכת לא מכירה: ממשיכים רק אם היא חתומה בתעודת הסינון של FilterPhone (הסינון באמצע
         * הדרך) ותקינה בכל השאר (שם האתר, תוקף). כל תעודה אחרת — נחסם, כמו בכל דפדפן.
         */
        @SuppressLint("WebViewClientOnReceivedSslError")
        override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
            if (signedByFilter(error)) handler.proceed() else {
                handler.cancel()
                if ((error.url == view.url || view.url == null) && t === tabs.getOrNull(current)) {
                    showNotice(getString(R.string.browser_ssl_error))
                }
            }
        }
    }

    private inner class Chrome(private val t: Tab) : WebChromeClient() {
        override fun onProgressChanged(view: WebView, newProgress: Int) {
            if (t === tabs.getOrNull(current)) progress.progress = newProgress
        }

        override fun onReceivedTitle(view: WebView, title: String?) {
            t.title = title.orEmpty()
        }

        override fun onReceivedIcon(view: WebView, icon: Bitmap?) {
            icon ?: return
            t.icon = icon
            IconCache.save(this@BrowserActivity, SafeSites.hostOf(view.url ?: return), icon)
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

    // ------------------------------------------------------------ עזרי תצוגה (דף הבית / חלונות — בקוד, בלי XML)

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun column(gravity: Int = Gravity.NO_GRAVITY) = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        this.gravity = gravity
    }

    private fun margins(top: Int = 0, bottom: Int = 0) =
        LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            topMargin = dp(top)
            bottomMargin = dp(bottom)
        }

    private fun text(s: String, sp: Float, bold: Boolean = false, sub: Boolean = false, accent: Boolean = false,
                     center: Boolean = false, lines: Int = 0) = TextView(this).apply {
        text = s
        setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
        setTextColor(getColor(if (accent) R.color.browser_accent else if (sub) R.color.browser_text_sub else R.color.browser_text))
        if (bold) typeface = Typeface.DEFAULT_BOLD
        textAlignment = if (center) View.TEXT_ALIGNMENT_CENTER else View.TEXT_ALIGNMENT_VIEW_START
        if (lines > 0) {
            maxLines = lines
            ellipsize = TextUtils.TruncateAt.END
        }
    }

    private fun rounded(color: Int, radius: Float) = GradientDrawable().apply {
        setColor(color)
        cornerRadius = radius
    }

    private fun oval(color: Int) = GradientDrawable().apply {
        shape = GradientDrawable.OVAL
        setColor(color)
    }

    /** הסמלים של האתרים (favicon) — נשמרים בטלפון כדי שדף הבית יציג אותם גם בלי לפתוח את האתר. */
    private object IconCache {
        private fun file(context: Context, host: String) =
            File(File(context.cacheDir, "browser_icons").apply { mkdirs() }, host.removePrefix("www.").replace(Regex("[^a-z0-9.-]"), "_") + ".png")

        fun load(context: Context, host: String?): Bitmap? {
            host ?: return null
            val f = file(context, host)
            return if (f.isFile) runCatching { BitmapFactory.decodeFile(f.path) }.getOrNull() else null
        }

        fun save(context: Context, host: String?, icon: Bitmap) {
            host ?: return
            if (icon.width < 16) return
            val app = context.applicationContext
            Thread {
                runCatching { file(app, host).outputStream().use { icon.compress(Bitmap.CompressFormat.PNG, 100, it) } }
            }.start()
        }
    }

    companion object {
        private const val TAG = "FilterPhone"
        private const val REQ_FILE = 41
        private const val GATE_GRACE_MS = 4_000L
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
