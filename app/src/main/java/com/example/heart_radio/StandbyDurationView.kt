package com.example.heart_radio

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.view.Gravity
import android.widget.*
import org.json.JSONObject

/** Saved preference is separate from starting/stopping standby. */
class StandbyDurationView(context: Context) : LinearLayout(context) {
    private val current = TextView(context).apply { textSize = 11f; setTextColor(WatchUi.muted) }

    internal fun showCurrent(state: JSONObject) {
        val status = currentLabel(state) + " · " + state.optString("syncStatus")
        if (current.text.toString() != status) current.text = status
    }

    companion object {
        internal fun currentLabel(state: JSONObject): String {
            val duration = when {
                !state.optBoolean("enabled") -> "已关闭"
                state.isNull("remainingMs") -> "不限时"
                else -> "剩余 ${(state.getLong("remainingMs") + 999) / 1000} 秒"
            }
            return "本次交互：$duration"
        }
    }

    init {
        orientation = VERTICAL
        fun dp(value: Int) = (value * resources.displayMetrics.density + .5f).toInt()
        fun label(value: String) = TextView(context).apply { text = value; textSize = 11f; setTextColor(WatchUi.muted) }
        addView(current)
        addView(label("本端开启偏好"), LayoutParams(-1,-2).apply { topMargin = dp(8) })
        val saved = CapabilityBridgeService.duration(context)
        val follow = CheckBox(context).apply {
            text = "跟随电脑设置"; textSize = 12f; setTextColor(Color.WHITE)
            buttonTintList = ColorStateList.valueOf(WatchUi.accent)
            isChecked = false
            contentDescription = "待命跟随电脑设置"
        }
        addView(follow)
        follow.visibility=View.GONE // Shared gate carries the actual lease in either transport.
        val unlimited = CheckBox(context).apply {
            text = "不限时"; textSize = 12f; setTextColor(Color.WHITE)
            buttonTintList = ColorStateList.valueOf(WatchUi.accent); isChecked = saved == 0L
            contentDescription = "本端开启偏好不限时"
        }
        addView(unlimited)
        addView(label("限制时长"))
        val amount = EditText(context).apply {
            textSize = 14f; setTextColor(Color.WHITE); setSingleLine(true)
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            setBackgroundColor(WatchUi.surface); setPadding(dp(8),dp(8),dp(8),dp(8))
            contentDescription = "待命限制时长数值"
        }
        val unit = Spinner(context).apply {
            contentDescription = "待命时长单位"
            adapter = object : ArrayAdapter<String>(context, android.R.layout.simple_spinner_dropdown_item, listOf("分钟","小时")) {
                override fun getView(position: Int, convertView: View?, parent: ViewGroup): View =
                    TextView(context).apply {
                        text = getItem(position); textSize = 12f; setTextColor(Color.WHITE)
                        gravity = Gravity.CENTER_VERTICAL; setPadding(dp(4),0,0,0)
                    }
                override fun getDropDownView(position: Int, convertView: View?, parent: ViewGroup): View =
                    TextView(context).apply {
                        text = getItem(position); textSize = 12f; setTextColor(Color.WHITE)
                        setBackgroundColor(WatchUi.surface); gravity = Gravity.CENTER_VERTICAL
                        minHeight = dp(44); setPadding(dp(8),0,dp(8),0)
                    }
            }
        }
        val hours = saved > 0 && saved % 3_600_000 == 0L
        amount.setText((if (saved == 0L) 30.0 else saved / (if (hours) 3_600_000.0 else 60_000.0)).toString().removeSuffix(".0"))
        unit.setSelection(if (hours) 1 else 0)
        val row = LinearLayout(context).apply { orientation = HORIZONTAL }
        row.addView(amount, LayoutParams(0,dp(44),1f)); row.addView(unit,LayoutParams(dp(80),dp(44)))
        addView(row,LayoutParams(-1,-2))
        fun inputs() {
            unlimited.isEnabled = !follow.isChecked
            amount.isEnabled = !follow.isChecked && !unlimited.isChecked
            unit.isEnabled = amount.isEnabled
            row.alpha = if (amount.isEnabled) 1f else .45f
        }
        follow.setOnCheckedChangeListener { _, _ -> inputs() }
        unlimited.setOnCheckedChangeListener { _, _ -> inputs() }; inputs()
        val message = label("")
        val save = Button(context).apply {
            text = "保存设置"; textSize = 12f; isAllCaps = false
            WatchUi.action(this,primary=true)
            contentDescription = "保存待命时长"
            setOnClickListener {
                try {
                    val value = if (follow.isChecked) CapabilityBridgeService.duration(context) else
                        StandbyDuration.parse(unlimited.isChecked, if (unlimited.isChecked) 0.0 else amount.text.toString().toDouble(), unit.selectedItemPosition == 1)
                    if (!CapabilityBridgeService.saveDuration(context,value,follow.isChecked)) throw IllegalStateException("保存失败，请重试")
                    message.text = if (follow.isChecked) "已保存本端偏好为跟随电脑" else "已保存本端开启偏好"
                    showCurrent(AgentInteraction.state())
                } catch (_: NumberFormatException) { message.text = "请输入有效时长" }
                catch (e: IllegalArgumentException) { message.text = e.message }
                catch (e: IllegalStateException) { message.text = e.message }
            }
        }
        addView(save,LayoutParams(-1,dp(44)).apply { topMargin = dp(8) })
        addView(message)
        addView(label("从本端开启时使用此设置。交互开启期间保存，会更新两端本次交互的期限。"),LayoutParams(-1,-2).apply { topMargin = dp(6) })
    }
}
