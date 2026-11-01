package com.filterphone.app

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.InputType
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * "דיווח / שליחה לבדיקה" מכרום (הכפתור הצף ⋮ או לחיצה ארוכה על קישור / תמונה — ChromeBubble):
 *   🚩 לא ראוי — דיווח: נשלח למנהל (תמונה — מוסתרת אצלך כבר עכשיו).
 *   🔍 חסום אצלי ולדעתי תקין — בדיקה: נבדק מיד לפי הרמה שלך. מתאים — נפתח; לא מתאים — למה, ואפשר לבקש
 *      לעבור רמה או לבקש לפתוח בכל זאת (המנהל מחליט); ממש לא ראוי — חסום, בלי אפשרות.
 * POST /v1/device/web/report (WebDesk.device_request בשרת).
 */
class WebReportActivity : Activity() {
    private val io = Executors.newSingleThreadExecutor()
    private lateinit var col: LinearLayout

    private val kind by lazy { intent.getStringExtra(EXTRA_KIND) ?: ChromeReport.Kind.SITE.name }
    private val url by lazy { intent.getStringExtra(EXTRA_URL).orEmpty() }
    private val pageUrl by lazy { intent.getStringExtra(EXTRA_PAGE).orEmpty() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(22), dp(20), dp(22), dp(18))
            setBackgroundColor(Color.WHITE)
        }
        setContentView(ScrollView(this).apply { addView(col) })
        setFinishOnTouchOutside(true)
        showChoice()
    }

    override fun onDestroy() {
        io.shutdown()
        super.onDestroy()
    }

    private val isImage get() = kind == ChromeReport.Kind.IMAGE.name
    private val what get() = getString(when (kind) {
        ChromeReport.Kind.IMAGE.name -> R.string.web_report_what_image
        ChromeReport.Kind.LINK.name -> R.string.web_report_what_link
        else -> R.string.web_report_what_site
    })

    /** הבחירה: דיווח (לא ראוי) או בדיקה (חסום ולדעתי תקין). */
    private fun showChoice() {
        col.removeAllViews()
        col.addView(title(getString(R.string.web_report_title, what)))
        if (url.isNotBlank()) col.addView(urlLine(url))
        val note = EditText(this).apply {
            hint = getString(R.string.web_report_note_hint)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            setTextColor(Color.rgb(0x16, 0x20, 0x2A))
        }
        col.addView(note)
        col.addView(button(getString(R.string.web_report_bad), Color.rgb(0xC6, 0x28, 0x28)) {
            send("report", note.text.toString().trim())
        })
        col.addView(sub(getString(R.string.web_report_bad_sub, what)))
        col.addView(button(getString(R.string.web_report_check), Color.rgb(0x1F, 0x6F, 0xEB)) {
            send("open", note.text.toString().trim())
        })
        col.addView(sub(getString(R.string.web_report_check_sub)))
        col.addView(button(getString(R.string.web_report_close), null) { finish() })
        if (url.isBlank()) {
            col.addView(sub(getString(R.string.web_report_no_url)))
        }
    }

    private fun send(intent: String, note: String) {
        if (url.isBlank()) return
        col.removeAllViews()
        col.addView(title(getString(if (intent == "report") R.string.web_report_sending else R.string.web_report_checking)))
        col.addView(ProgressBar(this).apply { isIndeterminate = true })
        val body = JSONObject()
            .put("kind", if (isImage) "image" else "site")
            .put("intent", intent)
            .put("url", url.take(1000))
            .put("page_url", pageUrl.take(1000))
            .put("note", note.take(300))
        io.execute {
            val res = post("/v1/device/web/report", body)
            runOnUiThread { if (!isFinishing) showResult(res) }
        }
    }

    /** התשובה מהשרת: נפתח / למה לא (ואפשרויות) / חסום / נשלח. */
    private fun showResult(res: JSONObject?) {
        col.removeAllViews()
        if (res == null) {
            col.addView(title(getString(R.string.web_report_failed)))
            col.addView(button(getString(R.string.web_report_retry), Color.rgb(0x1F, 0x6F, 0xEB)) { showChoice() })
            col.addView(button(getString(R.string.web_report_close), null) { finish() })
            return
        }
        val result = res.optString("result")
        val icon = when (result) {
            "allowed" -> "✅"
            "reported" -> "🙏"
            "blocked" -> "⛔"
            else -> "🔒"
        }
        col.addView(TextView(this).apply {
            text = icon
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 34f)
            gravity = Gravity.CENTER
        })
        col.addView(title(res.optString("reason")))
        if (res.optBoolean("can_request") && result in setOf("not_in_level", "pending")) {
            val rid = res.optInt("review_id")
            col.addView(button(getString(R.string.web_report_open_anyway), Color.rgb(0x1F, 0x6F, 0xEB)) {
                done(getString(R.string.web_report_open_sent))
            })
            col.addView(sub(getString(R.string.web_report_open_anyway_sub)))
            col.addView(button(getString(R.string.web_report_change_level), Color.rgb(0x5E, 0x35, 0xB1)) {
                requestLevel(res.optString("title"), rid)
            })
            col.addView(sub(getString(R.string.web_report_change_level_sub)))
        }
        col.addView(button(getString(R.string.web_report_close), null) { finish() })
    }

    private fun requestLevel(title: String, rid: Int) {
        val note = getString(R.string.web_report_level_note, what, url, title.ifBlank { "—" }, rid)
        col.removeAllViews()
        col.addView(ProgressBar(this).apply { isIndeterminate = true })
        io.execute {
            val ok = post("/v1/device/requests", JSONObject().put("note", note.take(2900))) != null
            runOnUiThread {
                if (!isFinishing) done(getString(if (ok) R.string.web_report_level_sent else R.string.web_report_failed))
            }
        }
    }

    private fun done(text: String) {
        col.removeAllViews()
        col.addView(title(text))
        col.addView(button(getString(R.string.web_report_close), null) { finish() })
    }

    /** POST עם האסימון של המכשיר. null = נכשל. */
    private fun post(path: String, body: JSONObject): JSONObject? {
        val (api, token) = GuardPolicy.credentials(this) ?: return null
        return try {
            val conn = (URL("$api$path").openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 15_000
                readTimeout = 40_000                 // אתר חדש — הבדיקה (הורדה + תמונות) לוקחת כמה שניות
                doOutput = true
                setRequestProperty("Authorization", "Bearer $token")
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
            }
            try {
                conn.outputStream.use { it.write(body.toString().toByteArray()) }
                if (conn.responseCode !in 200..299) return null
                JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
            } finally {
                conn.disconnect()
            }
        } catch (e: Exception) {
            Log.w(TAG, "web report failed: ${e.javaClass.simpleName}")
            null
        }
    }

    private fun title(s: String) = TextView(this).apply {
        text = s
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
        setTextColor(Color.rgb(0x16, 0x20, 0x2A))
        setTypeface(typeface, android.graphics.Typeface.BOLD)
        gravity = Gravity.CENTER
        setPadding(0, dp(6), 0, dp(10))
    }

    private fun sub(s: String) = TextView(this).apply {
        text = s
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        setTextColor(Color.rgb(0x5F, 0x6C, 0x79))
        setPadding(dp(4), dp(4), dp(4), dp(6))
    }

    private fun urlLine(s: String) = TextView(this).apply {
        text = s
        textDirection = View.TEXT_DIRECTION_LTR
        maxLines = 2
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
        setTextColor(Color.rgb(0x5F, 0x6C, 0x79))
        setPadding(0, 0, 0, dp(8))
    }

    private fun button(s: String, color: Int?, onClick: () -> Unit) = Button(this).apply {
        text = s
        isAllCaps = false
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
        setTextColor(if (color != null) Color.WHITE else Color.rgb(0x1F, 0x6F, 0xEB))
        background = GradientDrawable().apply {
            cornerRadius = dp(12).toFloat()
            if (color != null) setColor(color) else {
                setColor(Color.WHITE)
                setStroke(dp(2), Color.rgb(0xD6, 0xDE, 0xE8))
            }
        }
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(52)).apply { topMargin = dp(10) }
        setOnClickListener { onClick() }
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    companion object {
        private const val TAG = "FilterPhone"
        private const val EXTRA_KIND = "com.filterphone.app.extra.REPORT_KIND"
        private const val EXTRA_URL = "com.filterphone.app.extra.REPORT_URL"
        private const val EXTRA_PAGE = "com.filterphone.app.extra.REPORT_PAGE"

        fun open(context: Context, kind: ChromeReport.Kind, url: String?, pageUrl: String?) {
            context.startActivity(Intent(context, WebReportActivity::class.java)
                .putExtra(EXTRA_KIND, kind.name)
                .putExtra(EXTRA_URL, url.orEmpty().take(1000))
                .putExtra(EXTRA_PAGE, pageUrl.orEmpty().take(1000))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP))
        }
    }
}
