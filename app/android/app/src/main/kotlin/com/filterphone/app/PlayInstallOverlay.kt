package com.filterphone.app

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView

/**
 * מסך ההתקנה של FilterPhone מעל Google Play (חלון ההתקנה — PlayLock): מכסה את Play כולו, כולל שורת המצב
 * וסרגל הניווט, כך שאי אפשר לגעת, לגלול או לנווט ב-Play. רק הכפתורים של המסך הזה פועלים. FilterPhone
 * לוחץ בעצמו על "התקנה" ב-Play (GuardService), והמסך מציג מה קורה: פותח / מתקין (אחוזים) / הותקן.
 *
 * שכבה של שירות הנגישות (TYPE_ACCESSIBILITY_OVERLAY) — בלי הרשאת "הצגה מעל אפליקציות". כל תקלה — בלי
 * מסך (חלון ההתקנה הרגיל עדיין מחזיר מכל ניווט החוצה).
 */
class PlayInstallOverlay(
    private val service: AccessibilityService,
    private val onAction: (Button) -> Unit,
) {
    enum class Button { INSTALL, CANCEL, BACK, OPEN, RETRY }

    /** מה מוצג: כותרת, שורת מצב, אחוז (null = בלי פס / פס לא קבוע), והכפתורים. */
    data class State(val status: String, val progress: Int?, val busy: Boolean, val buttons: List<Pair<Button, String>>)

    private val wm by lazy { service.getSystemService(Context.WINDOW_SERVICE) as WindowManager }
    private var root: FrameLayout? = null
    private var title: TextView? = null
    private var status: TextView? = null
    private var bar: ProgressBar? = null
    private var row: LinearLayout? = null
    private var shown: Pair<String, State>? = null

    val isShown: Boolean get() = root != null

    fun show(label: String, state: State) {
        if (shown == label to state && root != null) return
        try {
            if (root == null) {
                val v = build()
                wm.addView(v, params())
                root = v
            }
            title?.text = service.getString(R.string.play_overlay_title, label.ifBlank { service.getString(R.string.play_overlay_app) })
            status?.text = state.status
            bar?.apply {
                visibility = if (state.busy || state.progress != null) View.VISIBLE else View.GONE
                isIndeterminate = state.progress == null
                if (state.progress != null) progress = state.progress
            }
            row?.removeAllViews()
            state.buttons.forEachIndexed { i, (b, text) -> row?.addView(button(text, primary = i == 0) { onAction(b) }) }
            shown = label to state
        } catch (e: Exception) {
            Log.w(TAG, "play overlay failed", e)
            hide()
        }
    }

    fun hide() {
        val v = root ?: return
        root = null
        shown = null
        runCatching { wm.removeViewImmediate(v) }
    }

    private fun params() = WindowManager.LayoutParams(
        WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.OPAQUE,
    ).apply {
        gravity = Gravity.TOP or Gravity.START
        title = "FilterPhone play install"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) fitInsetsTypes = 0      // כל המסך, גם הסרגלים
    }

    private fun build(): FrameLayout {
        val col = LinearLayout(service).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(24), 0, dp(24), 0)
        }
        col.addView(TextView(service).apply {
            text = "🔒"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 40f)
            gravity = Gravity.CENTER
        })
        title = TextView(service).apply {
            setTextColor(Color.rgb(0x16, 0x20, 0x2A))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 22f)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            gravity = Gravity.CENTER
            setPadding(0, dp(12), 0, 0)
        }.also { col.addView(it) }
        status = TextView(service).apply {
            setTextColor(Color.rgb(0x44, 0x4B, 0x5A))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 17f)
            gravity = Gravity.CENTER
            setPadding(0, dp(10), 0, dp(18))
        }.also { col.addView(it) }
        bar = ProgressBar(service, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
            isIndeterminate = true
        }.also { col.addView(it, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(10))) }
        row = LinearLayout(service).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(28), 0, 0)
        }.also { col.addView(it, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)) }
        col.addView(TextView(service).apply {
            setText(R.string.play_overlay_note)
            setTextColor(Color.rgb(0x8A, 0x91, 0x9E))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            gravity = Gravity.CENTER
            setPadding(0, dp(22), 0, 0)
        })
        return FrameLayout(service).apply {
            setBackgroundColor(Color.WHITE)
            isClickable = true                       // בולע כל נגיעה — שום דבר לא עובר ל-Play
            isFocusable = false
            addView(col, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.CENTER))
        }
    }

    private fun button(text: String, primary: Boolean, onClick: () -> Unit): android.widget.Button =
        android.widget.Button(service).apply {
            this.text = text
            isAllCaps = false
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 17f)
            setTextColor(if (primary) Color.WHITE else Color.rgb(0x1F, 0x6F, 0xEB))
            background = GradientDrawable().apply {
                cornerRadius = dp(14).toFloat()
                if (primary) setColor(Color.rgb(0x1F, 0x6F, 0xEB)) else {
                    setColor(Color.WHITE)
                    setStroke(dp(2), Color.rgb(0xD6, 0xDE, 0xE8))
                }
            }
            minHeight = dp(56)
            setOnClickListener { onClick() }
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(56)).apply {
                topMargin = dp(10)
            }
        }

    private fun dp(v: Int): Int = (v * service.resources.displayMetrics.density).toInt()

    private companion object {
        const val TAG = "FilterPhone"
    }
}
