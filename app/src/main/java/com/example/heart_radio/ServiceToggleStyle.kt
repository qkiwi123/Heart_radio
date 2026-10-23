package com.example.heart_radio

import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.graphics.drawable.StateListDrawable
import android.widget.Button

/** Color describes whether the service is running, including reconnecting; not PC connectivity. */
object ServiceToggleStyle {
    fun configure(button: Button) {
        val density = button.resources.displayMetrics.density
        fun surface(fill: Int, border: Int) = GradientDrawable().apply {
            setColor(fill)
            cornerRadius = 10 * density
            setStroke((density + 0.5f).toInt().coerceAtLeast(1), border)
        }
        val content = StateListDrawable().apply {
            addState(intArrayOf(android.R.attr.state_selected),
                surface(WatchUi.selected, WatchUi.accent))
            addState(intArrayOf(), surface(WatchUi.surface, WatchUi.border))
        }
        button.backgroundTintList = null
        button.background = RippleDrawable(ColorStateList.valueOf(Color.argb(65, 255, 255, 255)), content, null)
        button.setTextColor(ColorStateList(
            arrayOf(intArrayOf(-android.R.attr.state_enabled), intArrayOf(android.R.attr.state_selected), intArrayOf()),
            intArrayOf(WatchUi.muted, Color.WHITE, Color.rgb(239, 233, 237))))
    }
}
