package com.filterphone.app

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * קישור מוואטסאפ, ובשיחה יש כמה קישורים (הדפדפן הפנימי של וואטסאפ הראה רק את המעטפת w.meta.me, שלא
 * עובדת בכרום): "איזה קישור לפתוח?" — לחיצה פותחת אותו ב-Chrome (שם הוא מסונן).
 */
class LinkChooserActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val links = intent.getStringArrayListExtra(EXTRA_LINKS).orEmpty().take(MAX)
        if (links.isEmpty()) { finish(); return }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(18), dp(20), dp(16))
            setBackgroundColor(Color.WHITE)
        }
        col.addView(TextView(this).apply {
            setText(R.string.link_chooser_title)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
            setTextColor(Color.rgb(0x16, 0x20, 0x2A))
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, dp(10))
        })
        for (link in links) {
            col.addView(Button(this).apply {
                text = link.substringAfter("://").take(90)
                textDirection = View.TEXT_DIRECTION_LTR
                isAllCaps = false
                maxLines = 2
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
                setTextColor(Color.rgb(0x1F, 0x6F, 0xEB))
                background = GradientDrawable().apply {
                    cornerRadius = dp(12).toFloat()
                    setColor(Color.WHITE)
                    setStroke(dp(2), Color.rgb(0xD6, 0xDE, 0xE8))
                }
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(8) }
                minHeight = dp(52)
                setOnClickListener {
                    LinkRelayActivity.openInChrome(this@LinkChooserActivity, link)
                    finish()
                }
            })
        }
        col.addView(Button(this).apply {
            setText(R.string.web_report_close)
            isAllCaps = false
            setOnClickListener { finish() }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(12) }
        })
        setContentView(ScrollView(this).apply { addView(col) })
        setFinishOnTouchOutside(true)
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    companion object {
        private const val EXTRA_LINKS = "com.filterphone.app.extra.LINKS"
        private const val MAX = 8

        fun open(context: Context, links: List<String>) {
            context.startActivity(Intent(context, LinkChooserActivity::class.java)
                .putStringArrayListExtra(EXTRA_LINKS, ArrayList(links.take(MAX)))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP))
        }
    }
}
