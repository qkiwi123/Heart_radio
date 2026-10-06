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
                surface(Color.rgb(105, 227, 188), Color.rgb(151, 241, 214)))
            addState(intArrayOf(), surface(Color.rgb(38, 54, 47), Color.rgb(74, 96, 84)))
        }
        button.backgroundTintList = null
        button.background = RippleDrawable(ColorStateList.valueOf(Color.argb(65, 255, 255, 255)), content, null)
        button.setTextColor(ColorStateList(
            arrayOf(intArrayOf(-android.R.attr.state_enabled), intArrayOf(android.R.attr.state_selected), intArrayOf()),
            intArrayOf(Color.rgb(170, 190, 182), Color.rgb(11, 37, 29), Color.rgb(223, 233, 227))))
    }
}
