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
import android.os.SystemClock
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import kotlin.math.abs
import kotlin.math.min

/**
 * הכיסוי של תמונות הפרופיל בוואטסאפ (ראו WaAvatars): חלון שקוף על כל המסך, ועליו עיגולים לבנים (ובזריקה
 * מהירה — פסים) בדיוק במקומות של התמונות. שכבה של שירות הנגישות (TYPE_ACCESSIBILITY_OVERLAY) — בלי הרשאת
 * "הצגה מעל אפליקציות". לא לחיץ ולא מקבל פוקוס: כל נגיעה עוברת לוואטסאפ כרגיל. במצב כהה — בצבע הרקע של
 * וואטסאפ. כל תקלה — פשוט בלי כיסוי.
 *
 * תנועה חלקה בגלילה: המיקום נמדד ~30 פעמים בשנייה (GuardService), ובין מדידה למדידה הכיסוי ממשיך לזוז בכל
 * פריים לפי מהירות הגלילה (ניבוי קצר, עד [MAX_AHEAD_MS]) — כך הוא "צמוד" לתמונה ולא רודף אחריה. בגלילה
 * מהירה העיגול נמתח קצת בכיוון התנועה ([STRETCH_MS]) כדי שהתמונה לא תציץ, וחוזר לעיגול כשהגלילה נרגעת.
 * עיגול שיוצא מגבולות הרשימה — נחתך ונעלם יחד עם התמונה.
 */
class AvatarMask(private val service: AccessibilityService) {
    private val wm by lazy { service.getSystemService(Context.WINDOW_SERVICE) as WindowManager }
    private var view: MaskView? = null

    val isShown: Boolean get() = view != null

    /**
     * [vx]/[vy] — מהירות הגלילה (פיקסלים למילישנייה) של הרשימות, [at] — מתי נמדדו המיקומים
     * (SystemClock.uptimeMillis). 0 — בלי תנועה.
     */
    fun show(boxes: List<WaAvatars.Box>, vx: Float = 0f, vy: Float = 0f, at: Long = 0L) {
        if (boxes.isEmpty()) {
            hide()
            return
        }
        try {
            val v = view ?: MaskView(service).also {
                wm.addView(it, params())
                view = it
            }
            v.update(boxes, dark(), vx, vy, if (at == 0L) SystemClock.uptimeMillis() else at)
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
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or
            WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
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
        private var vx = 0f
        private var vy = 0f
        private var at = 0L
        private val loc = IntArray(2)
        private val rect = RectF()

        init {
            importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        }

        fun update(next: List<WaAvatars.Box>, dark: Boolean, vx: Float, vy: Float, at: Long) {
            val color = if (dark) Color.rgb(0x11, 0x1B, 0x21) else Color.WHITE
            val moving = vx != 0f || vy != 0f || this.vx != 0f || this.vy != 0f
            if (next == boxes && paint.color == color && !moving) return
            boxes = next
            paint.color = color
            this.vx = vx
            this.vy = vy
            this.at = at
            invalidate()
        }

        override fun onDraw(canvas: Canvas) {
            getLocationOnScreen(loc)
            val ahead = (SystemClock.uptimeMillis() - at + LEAD_MS).coerceIn(0L, MAX_AHEAD_MS).toFloat()
            val ox = vx * ahead
            val oy = vy * ahead
            val ex = min(abs(vx) * STRETCH_MS, MAX_STRETCH_PX)
            val ey = min(abs(vy) * STRETCH_MS, MAX_STRETCH_PX)
            for (b in boxes) {
                val dx = if (b.follow) ox else 0f
                val dy = if (b.follow) oy else 0f
                val sx = if (b.follow) ex else 0f
                val sy = if (b.follow) ey else 0f
                rect.set(b.left - loc[0] + dx - sx, b.top - loc[1] + dy - sy,
                    b.right - loc[0] + dx + sx, b.bottom - loc[1] + dy + sy)
                val clip = b.clip
                if (clip != null) {
                    canvas.save()
                    canvas.clipRect((clip.left - loc[0]).toFloat(), (clip.top - loc[1]).toFloat(),
                        (clip.right - loc[0]).toFloat(), (clip.bottom - loc[1]).toFloat())
                }
                if (b.round) {
                    val r = min(rect.width(), rect.height()) / 2f            // נמתח לגלולה, לא לאליפסה
                    canvas.drawRoundRect(rect, r, r, paint)
                } else {
                    canvas.drawRect(rect, paint)
                }
                if (clip != null) canvas.restore()
            }
            // ממשיכים לזוז עם הגלילה עד המדידה הבאה (ולא יותר מ-MAX_AHEAD_MS קדימה)
            if ((vx != 0f || vy != 0f) && SystemClock.uptimeMillis() - at < MAX_AHEAD_MS) postInvalidateOnAnimation()
        }
    }

    companion object {
        private const val TAG = "FilterPhone"
        /** כמה קדימה לצייר (הפריים מוצג על המסך קצת אחרי שצויר). */
        private const val LEAD_MS = 16L
        /** ניבוי לכל היותר כזה — אם לא הגיעה מדידה חדשה, עוצרים במקום (לא "בורחים" מהתמונה). */
        private const val MAX_AHEAD_MS = 90L
        /** מתיחה בכיוון התנועה: מהירות × זמן — מכסה את הפער בין המדידה למסך. */
        private const val STRETCH_MS = 22f
        private const val MAX_STRETCH_PX = 160f
    }
}
