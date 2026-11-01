package com.filterphone.app

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.TextView

/**
 * Google Play מנוהל (PlayManaged): מעל כל כפתור "התקנה" של אפליקציה שאינה מאושרת — תווית "🔒 חסום להתקנה ·
 * בקשת אישור" באותו מקום ובאותו גודל. הלחיצה עליה פותחת את חלון הבקשה; הכפתור של Play מתחתיה לא נלחץ.
 * שכבה של שירות הנגישות (TYPE_ACCESSIBILITY_OVERLAY). כל תקלה — בלי תווית (ההסרה אחרי ההתקנה עדיין שומרת).
 */
class PlayRequestBadges(private val service: AccessibilityService, private val onTap: () -> Unit) {
    private val wm by lazy { service.getSystemService(Context.WINDOW_SERVICE) as WindowManager }
    private val views = ArrayList<Pair<View, Rect>>()

    val isShown: Boolean get() = views.isNotEmpty()

    /** מציג תווית מעל כל אחד מהמלבנים (במסך), ומסיר את מה שכבר לא צריך. */
    fun show(rects: List<Rect>) {
        val want = rects.filter { it.width() > 0 && it.height() > 0 }.take(MAX)
        if (want == views.map { it.second }) return
        hide()
        for (r in want) {
            try {
                val v = build()
                wm.addView(v, params(r))
                views += v to Rect(r)
            } catch (e: Exception) {
                Log.w(TAG, "play badge failed", e)
            }
        }
    }

    fun hide() {
        views.forEach { (v, _) -> runCatching { wm.removeViewImmediate(v) } }
        views.clear()
    }

    private fun params(r: Rect) = WindowManager.LayoutParams(
        r.width(), r.height(),
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP or Gravity.START
        x = r.left
        y = r.top
        title = "FilterPhone play badge"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) fitInsetsTypes = 0     // מיקום כמו getBoundsInScreen
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }
    }

    private fun build(): View = TextView(service).apply {
        setText(R.string.play_badge_blocked)
        setTextColor(Color.WHITE)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        gravity = Gravity.CENTER
        maxLines = 2
        background = GradientDrawable().apply {
            cornerRadius = 20f * service.resources.displayMetrics.density
            setColor(Color.rgb(0xC6, 0x28, 0x28))
        }
        isClickable = true
        setOnClickListener { onTap() }
    }

    private companion object {
        const val TAG = "FilterPhone"
        const val MAX = 8
    }
}
