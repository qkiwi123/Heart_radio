package com.example.heart_radio

import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.view.Gravity
import android.widget.Button

/** Opaque surfaces also avoid the API-27 watch's hardware text rendering issue. */
object WatchUi {
    val canvas = Color.BLACK
    val surface = Color.BLACK
    val accent = Color.rgb(255, 82, 104)
    val primary = Color.rgb(182, 34, 55)
    val muted = Color.rgb(168, 168, 168)
    val border = Color.rgb(55, 55, 55)
    val selected = Color.BLACK
    fun action(button: Button, primary: Boolean = false, heightDp: Int = 44) = with(button) {
        fun dp(v: Int) = (v * resources.displayMetrics.density + .5f).toInt()
        isAllCaps = false; includeFontPadding = false; textSize = 13f
        minimumWidth = 0; minWidth = 0; minimumHeight = dp(heightDp); minHeight = dp(heightDp)
        val verticalPadding = if (heightDp < 44) 3 else 6
        gravity = Gravity.CENTER; setPadding(dp(6), dp(verticalPadding), dp(6), dp(verticalPadding))
        backgroundTintList = null
        setTextColor(ColorStateList(arrayOf(intArrayOf(-android.R.attr.state_enabled), intArrayOf()),
            intArrayOf(muted, Color.WHITE)))
        background = RippleDrawable(ColorStateList.valueOf(Color.argb(65, 255, 255, 255)),
            GradientDrawable().apply {
                setColor(if (primary) WatchUi.primary else surface); cornerRadius = dp(12).toFloat()
                if (!primary) setStroke(dp(1), border)
            }, null)
    }
}
