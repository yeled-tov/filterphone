package com.filterphone.app

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.content.res.Configuration
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.RectF
import android.os.Build
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowManager

/**
 * הכיסוי של תמונות הפרופיל בוואטסאפ (ראו WaAvatars): חלון שקוף על כל המסך, ועליו עיגולים לבנים (ובזמן גלילה —
 * פסים) בדיוק במקומות של התמונות. שכבה של שירות הנגישות (TYPE_ACCESSIBILITY_OVERLAY) — בלי הרשאת "הצגה מעל
 * אפליקציות". לא לחיץ ולא מקבל פוקוס: כל נגיעה עוברת לוואטסאפ כרגיל (לחיצה על תמונה שפותחת אותה בגדול —
 * WaProfileDetector יוצא מהמסך). במצב כהה — בצבע הרקע של וואטסאפ. כל תקלה — פשוט בלי כיסוי.
 */
class AvatarMask(private val service: AccessibilityService) {
    private val wm by lazy { service.getSystemService(Context.WINDOW_SERVICE) as WindowManager }
    private var view: MaskView? = null

    val isShown: Boolean get() = view != null

    fun show(boxes: List<WaAvatars.Box>) {
        if (boxes.isEmpty()) {
            hide()
            return
        }
        try {
            val v = view ?: MaskView(service).also {
                wm.addView(it, params())
                view = it
            }
            v.update(boxes, dark())
        } catch (e: Exception) {
            Log.w(TAG, "avatar mask failed", e)
            hide()
        }
    }

    fun hide() {
        val v = view ?: return
        view = null
        runCatching { wm.removeViewImmediate(v) }
    }

    private fun dark(): Boolean =
        (service.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES

    private fun params() = WindowManager.LayoutParams(
        WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP or Gravity.START
        title = "FilterPhone avatar mask"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) fitInsetsTypes = 0     // כל המסך — כמו getBoundsInScreen
    }

    /** מצייר את הכיסויים במיקום שלהם במסך (מתקן לפי המיקום של החלון עצמו, למקרה שאינו מתחיל ב-0,0). */
    private class MaskView(context: Context) : View(context) {
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
        private var boxes: List<WaAvatars.Box> = emptyList()
        private val loc = IntArray(2)
        private val rect = RectF()

        init {
            importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        }

        fun update(next: List<WaAvatars.Box>, dark: Boolean) {
            val color = if (dark) Color.rgb(0x11, 0x1B, 0x21) else Color.WHITE
            if (next == boxes && paint.color == color) return
            boxes = next
            paint.color = color
            invalidate()
        }

        override fun onDraw(canvas: Canvas) {
            getLocationOnScreen(loc)
            for (b in boxes) {
                rect.set((b.left - loc[0]).toFloat(), (b.top - loc[1]).toFloat(),
                    (b.right - loc[0]).toFloat(), (b.bottom - loc[1]).toFloat())
                if (b.round) canvas.drawOval(rect, paint) else canvas.drawRect(rect, paint)
            }
        }
    }

    companion object {
        private const val TAG = "FilterPhone"
    }
}
