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
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * "האפליקציה הזו חסומה להתקנה — בקשת אישור" (Google Play מנוהל — PlayManaged): שם האפליקציה (מולא מראש,
 * אפשר לתקן) ולמה צריך אותה. נשלח כבקשת שינוי לשרת (כמו "בקשת אפליקציה" בחנות) — המנהל רואה בבקשות,
 * בודק, ואם מאשר מוסיף לחנות FilterPhone. חלון קטן מעל Play, בלי תלות במסך הראשי של האפליקציה.
 */
class AppRequestActivity : Activity() {
    private val io = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val title = intent.getStringExtra(EXTRA_TITLE).orEmpty()
        val pkg = intent.getStringExtra(EXTRA_PACKAGE)
        val removed = intent.getBooleanExtra(EXTRA_REMOVED, false)

        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(22), dp(22), dp(22), dp(18))
            setBackgroundColor(Color.WHITE)
        }
        col.addView(text("🔒", 34f, Color.BLACK).apply { gravity = Gravity.CENTER })
        col.addView(text(getString(if (removed) R.string.app_request_removed_title else R.string.app_request_title,
            title.ifBlank { getString(R.string.play_overlay_app) }), 20f, Color.rgb(0x16, 0x20, 0x2A)).apply {
            gravity = Gravity.CENTER
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(0, dp(8), 0, dp(6))
        })
        col.addView(text(getString(R.string.app_request_sub), 15f, Color.rgb(0x5F, 0x6C, 0x79)).apply {
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, dp(16))
        })
        val name = input(getString(R.string.app_request_name_hint), title, multiLine = false)
        val why = input(getString(R.string.app_request_why_hint), "", multiLine = true)
        col.addView(label(getString(R.string.app_request_name)))
        col.addView(name)
        col.addView(label(getString(R.string.app_request_why)))
        col.addView(why)
        val send = button(getString(R.string.app_request_send), primary = true)
        val close = button(getString(R.string.app_request_close), primary = false)
        col.addView(send)
        col.addView(close)
        setContentView(ScrollView(this).apply { addView(col) })
        setFinishOnTouchOutside(false)

        close.setOnClickListener { finish() }
        send.setOnClickListener {
            val app = name.text.toString().trim()
            val reason = why.text.toString().trim()
            if (app.isEmpty()) { name.error = getString(R.string.app_request_name_needed); return@setOnClickListener }
            if (reason.length < 3) { why.error = getString(R.string.app_request_why_needed); return@setOnClickListener }
            send.isEnabled = false
            val note = buildString {
                append("בקשת אפליקציה מ-Google Play: ").append(app)
                if (!pkg.isNullOrBlank()) append(" (").append(pkg).append(")")
                append("\nלמה צריך אותה: ").append(reason)
                if (removed) append("\n(הותקנה מ-Play והוסרה אוטומטית — לא מאושרת)")
            }
            io.execute {
                val ok = submit(note)
                runOnUiThread {
                    Toast.makeText(this, if (ok) R.string.app_request_sent else R.string.app_request_failed,
                        Toast.LENGTH_LONG).show()
                    if (ok) finish() else send.isEnabled = true
                }
            }
        }
    }

    override fun onDestroy() {
        io.shutdown()
        super.onDestroy()
    }

    /** POST /v1/device/requests עם האסימון של המכשיר (אותו של המגן). */
    private fun submit(note: String): Boolean {
        val (api, token) = GuardPolicy.credentials(this) ?: return false
        return try {
            val conn = (URL("$api/v1/device/requests").openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 15_000
                readTimeout = 15_000
                doOutput = true
                setRequestProperty("Authorization", "Bearer $token")
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
            }
            try {
                conn.outputStream.use { it.write(JSONObject().put("note", note.take(2900)).toString().toByteArray()) }
                conn.responseCode in 200..299
            } finally {
                conn.disconnect()
            }
        } catch (e: Exception) {
            Log.w(TAG, "app request failed: ${e.javaClass.simpleName}")
            false
        }
    }

    private fun text(s: String, sp: Float, color: Int) = TextView(this).apply {
        text = s
        setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
        setTextColor(color)
    }

    private fun label(s: String) = text(s, 14f, Color.rgb(0x5F, 0x6C, 0x79)).apply { setPadding(0, dp(10), 0, dp(4)) }

    private fun input(hint: String, value: String, multiLine: Boolean) = EditText(this).apply {
        this.hint = hint
        setText(value)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
        setTextColor(Color.rgb(0x16, 0x20, 0x2A))
        inputType = if (multiLine) InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or
            InputType.TYPE_TEXT_FLAG_CAP_SENTENCES else InputType.TYPE_CLASS_TEXT
        minLines = if (multiLine) 3 else 1
        gravity = Gravity.TOP or Gravity.START
        minHeight = dp(48)
    }

    private fun button(s: String, primary: Boolean) = Button(this).apply {
        text = s
        isAllCaps = false
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 17f)
        setTextColor(if (primary) Color.WHITE else Color.rgb(0x1F, 0x6F, 0xEB))
        background = GradientDrawable().apply {
            cornerRadius = dp(12).toFloat()
            if (primary) setColor(Color.rgb(0x1F, 0x6F, 0xEB)) else {
                setColor(Color.WHITE)
                setStroke(dp(2), Color.rgb(0xD6, 0xDE, 0xE8))
            }
        }
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(52)).apply { topMargin = dp(12) }
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    companion object {
        private const val TAG = "FilterPhone"
        private const val EXTRA_TITLE = "com.filterphone.app.extra.APP_TITLE"
        private const val EXTRA_PACKAGE = "com.filterphone.app.extra.APP_PACKAGE"
        private const val EXTRA_REMOVED = "com.filterphone.app.extra.APP_REMOVED"

        fun open(context: Context, title: String, pkg: String?, removed: Boolean) {
            context.startActivity(Intent(context, AppRequestActivity::class.java)
                .putExtra(EXTRA_TITLE, title.take(80))
                .putExtra(EXTRA_PACKAGE, pkg)
                .putExtra(EXTRA_REMOVED, removed)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP))
        }
    }
}
