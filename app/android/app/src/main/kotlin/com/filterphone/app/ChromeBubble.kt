package com.filterphone.app

import android.accessibilityservice.AccessibilityService
import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.TextView
import kotlin.math.abs

/**
 * בכרום (כמו בנטפרי): כפתור ⋮ קטן וצף בצד המסך — לחיצה פותחת "דיווח / שליחה לבדיקה" על הדף הנוכחי
 * (WebReportActivity). אפשר לגרור אותו למעלה / למטה (המיקום נשמר). ובלחיצה ארוכה על קישור או תמונה —
 * ליד התפריט של כרום עולה תווית "🚩 דיווח / בדיקה ב-FilterPhone" לקישור / לתמונה שנבחרו.
 * שכבה של שירות הנגישות (TYPE_ACCESSIBILITY_OVERLAY). כל תקלה — בלי כפתור (הכרום עצמו לא מושפע).
 */
class ChromeBubble(
    private val service: AccessibilityService,
    private val onBubble: () -> Unit,
    private val onChip: () -> Unit,
) {
    private val wm by lazy { service.getSystemService(Context.WINDOW_SERVICE) as WindowManager }
    private val prefs by lazy { service.getSharedPreferences(PREFS, Context.MODE_PRIVATE) }
    private var bubble: View? = null
    private var bubbleParams: WindowManager.LayoutParams? = null
    private var chip: View? = null

    val isShown: Boolean get() = bubble != null

    fun show() {
        if (bubble != null) return
        try {
            val v = buildBubble()
            val p = params(dp(SIZE), dp(SIZE)).apply {
                gravity = Gravity.TOP or Gravity.END
                x = 0
                y = prefs.getInt(K_Y, (service.resources.displayMetrics.heightPixels * 0.42).toInt())
            }
            wm.addView(v, p)
            bubble = v
            bubbleParams = p
        } catch (e: Exception) {
            Log.w(TAG, "chrome bubble failed", e)
            bubble = null
        }
    }

    fun hide() {
        bubble?.let { runCatching { wm.removeViewImmediate(it) } }
        bubble = null
        hideChip()
    }

    /** התווית ליד תפריט הלחיצה הארוכה של כרום. */
    fun showChip(text: String) {
        hideChip()
        try {
            val v = TextView(service).apply {
                this.text = text
                setTextColor(Color.WHITE)
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
                gravity = Gravity.CENTER
                setPadding(dp(18), dp(12), dp(18), dp(12))
                background = GradientDrawable().apply {
                    cornerRadius = dp(24).toFloat()
                    setColor(Color.rgb(0xC6, 0x28, 0x28))
                }
                isClickable = true
                setOnClickListener { onChip() }
            }
            val p = params(WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT).apply {
                gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
                y = dp(28)
            }
            wm.addView(v, p)
            chip = v
        } catch (e: Exception) {
            Log.w(TAG, "chrome chip failed", e)
            chip = null
        }
    }

    fun hideChip() {
        chip?.let { runCatching { wm.removeViewImmediate(it) } }
        chip = null
    }

    val chipShown: Boolean get() = chip != null

    private fun params(w: Int, h: Int) = WindowManager.LayoutParams(
        w, h,
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
        PixelFormat.TRANSLUCENT,
    ).apply {
        title = "FilterPhone chrome report"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }
    }

    /** הכפתור הצף: לחיצה — דיווח; גרירה למעלה / למטה — מזיזה אותו (המיקום נשמר). */
    @SuppressLint("ClickableViewAccessibility")
    private fun buildBubble(): View = TextView(service).apply {
        text = "⋮"
        contentDescription = service.getString(R.string.web_report_bubble)
        setTextColor(Color.WHITE)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f)
        gravity = Gravity.CENTER
        alpha = 0.82f
        background = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(Color.rgb(0x1F, 0x6F, 0xEB))
        }
        var downY = 0f
        var startY = 0
        var moved = false
        setOnTouchListener { _, e ->
            val p = bubbleParams ?: return@setOnTouchListener false
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> { downY = e.rawY; startY = p.y; moved = false; true }
                MotionEvent.ACTION_MOVE -> {
                    val dy = (e.rawY - downY).toInt()
                    if (abs(dy) > dp(6)) moved = true
                    if (moved) {
                        val max = service.resources.displayMetrics.heightPixels - dp(SIZE + 40)
                        p.y = (startY + dy).coerceIn(dp(60), max)
                        runCatching { wm.updateViewLayout(this, p) }
                    }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    if (moved) prefs.edit().putInt(K_Y, p.y).apply() else performClick()
                    true
                }
                else -> false
            }
        }
        setOnClickListener { onBubble() }
    }

    private fun dp(v: Int): Int = (v * service.resources.displayMetrics.density).toInt()

    private companion object {
        const val TAG = "FilterPhone"
        const val PREFS = "fp_chrome_bubble"
        const val K_Y = "y"
        const val SIZE = 40
    }
}
