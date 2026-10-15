package com.filterphone.app

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.os.Build
import android.util.DisplayMetrics
import android.util.Log
import android.util.TypedValue
import android.view.Display
import android.view.Gravity
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * "דף לבן" על Google Play בזמן חלון ההתקנה (שלב א'): מכסה את כל מה שמתחת לכפתור ההתקנה בעמוד של
 * האפליקציה שנבחרה — צילומי מסך, "אפליקציות דומות", מודעות. הדף לא לחיץ, כך שאי אפשר לגלול ממנו
 * לאפליקציות אחרות. ראש העמוד (שם האפליקציה) והכפתור (התקנה / עדכון / פתיחה) נשארים גלויים ולחיצים.
 *
 * שכבה של שירות הנגישות (TYPE_ACCESSIBILITY_OVERLAY) — לא צריך הרשאת "הצגה מעל אפליקציות". לא מכסה
 * את שורת המצב ואת סרגל הניווט: "חזרה" ו"בית" תמיד עובדים. נסגרת ברגע שיוצאים מהעמוד / מ-Play / החלון
 * נגמר (GuardService). כל תקלה — פשוט בלי דף (החסימה הרגילה של החלון עדיין פועלת).
 */
class PlayCurtain(private val service: AccessibilityService) {
    private val wm by lazy { service.getSystemService(Context.WINDOW_SERVICE) as WindowManager }
    private var view: View? = null
    private var shownTop = -1

    val isShown: Boolean get() = view != null

    /** הגובה של המסך (בפיקסלים, כמו getBoundsInScreen) — ל-PlayLock.curtainTop. */
    fun screenHeight(): Int = realMetrics().heightPixels

    /** רווח קטן מתחת לכפתור, בפיקסלים. */
    fun marginPx(): Int = dp(10)

    /** מציג / מזיז את הדף כך שיתחיל ב-[top] (פיקסלים במסך) ויגיע עד מעל סרגל הניווט. */
    fun show(top: Int) {
        val bottom = usableBottom()
        val height = bottom - top
        if (height < dp(48)) {               // כמעט אין מה לכסות — לא מציגים פס דק
            hide()
            return
        }
        if (view != null && top == shownTop) return
        val lp = params(top, height)
        try {
            val v = view
            if (v == null) {
                val nv = build()
                wm.addView(nv, lp)
                view = nv
            } else {
                wm.updateViewLayout(v, lp)
            }
            shownTop = top
        } catch (e: Exception) {
            Log.w(TAG, "play curtain failed", e)
            hide()
        }
    }

    fun hide() {
        val v = view ?: return
        view = null
        shownTop = -1
        runCatching { wm.removeViewImmediate(v) }
    }

    private fun params(top: Int, height: Int) = WindowManager.LayoutParams(
        WindowManager.LayoutParams.MATCH_PARENT, height,
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
        PixelFormat.OPAQUE,
    ).apply {
        gravity = Gravity.TOP or Gravity.START
        y = top
        title = "FilterPhone play curtain"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            // המיקום במסך כמו getBoundsInScreen (בלי היסט של שורת המצב); בצדדים — לא על סרגל הניווט
            fitInsetsTypes = WindowInsets.Type.navigationBars()
            fitInsetsSides = WindowInsets.Side.LEFT or WindowInsets.Side.RIGHT
        }
    }

    /** הדף: לבן, לא לחיץ (בולע נגיעות וגלילה), עם הסבר קצר באמצע. */
    private fun build(): View {
        val text = LinearLayout(service).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            addView(TextView(service).apply {
                setText(R.string.guard_play_curtain_title)
                setTextColor(Color.rgb(0x44, 0x4B, 0x5A))
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
                gravity = Gravity.CENTER
            })
            addView(TextView(service).apply {
                setText(R.string.guard_play_curtain_sub)
                setTextColor(Color.rgb(0x8A, 0x91, 0x9E))
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
                gravity = Gravity.CENTER
                setPadding(0, dp(6), 0, 0)
            })
        }
        return FrameLayout(service).apply {
            setBackgroundColor(Color.WHITE)
            isClickable = true                       // בולע כל נגיעה — אין גלילה ואין לחיצה דרכו
            isFocusable = false
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
            setOnTouchListener { _, _ -> true }
            addView(text, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.TOP).apply { topMargin = dp(40) })
        }
    }

    /** התחתית של האזור שמותר לכסות: תחתית המסך פחות סרגל הניווט (אם יש כזה בתחתית). */
    private fun usableBottom(): Int {
        val real = realMetrics()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            runCatching {
                val m = wm.maximumWindowMetrics
                val nav = m.windowInsets.getInsetsIgnoringVisibility(WindowInsets.Type.navigationBars() or
                    WindowInsets.Type.tappableElement())
                return m.bounds.height() - nav.bottom
            }
        }
        // אנדרואיד 8–10: גובה האזור לאפליקציות (בלי סרגל הניווט שבתחתית)
        return minOf(real.heightPixels, service.resources.displayMetrics.heightPixels)
    }

    @Suppress("DEPRECATION")
    private fun realMetrics(): DisplayMetrics {
        val dm = DisplayMetrics()
        val display = (service.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager).getDisplay(Display.DEFAULT_DISPLAY)
        if (display != null) display.getRealMetrics(dm) else dm.setTo(service.resources.displayMetrics)
        return dm
    }

    private fun dp(v: Int): Int = (v * service.resources.displayMetrics.density).toInt()

    private companion object {
        const val TAG = "FilterPhone"
    }
}
