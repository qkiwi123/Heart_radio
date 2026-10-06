package com.example.heart_radio

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.view.Gravity
import android.widget.*

/** Saved preference is separate from starting/stopping standby. */
class StandbyDurationView(context: Context) : LinearLayout(context) {
    init {
        orientation = VERTICAL
        fun dp(value: Int) = (value * resources.displayMetrics.density + .5f).toInt()
        fun label(value: String) = TextView(context).apply { text = value; textSize = 11f; setTextColor(Color.rgb(170,190,182)) }
        val saved = CapabilityBridgeService.duration(context)
        val follow = CheckBox(context).apply {
            text = "跟随电脑设置"; textSize = 12f; setTextColor(Color.WHITE)
            buttonTintList = ColorStateList.valueOf(Color.rgb(105,227,188))
            isChecked = CapabilityBridgeService.followsComputer(context)
            contentDescription = "待命跟随电脑设置"
        }
        addView(follow)
        val unlimited = CheckBox(context).apply {
            text = "不限时"; textSize = 12f; setTextColor(Color.WHITE)
            buttonTintList = ColorStateList.valueOf(Color.rgb(105,227,188)); isChecked = saved == 0L
            contentDescription = "待命不限时"
        }
        addView(unlimited)
        addView(label("限制时长"))
        val amount = EditText(context).apply {
            textSize = 14f; setTextColor(Color.WHITE); setSingleLine(true)
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            setBackgroundColor(Color.rgb(38,54,47)); setPadding(dp(8),dp(8),dp(8),dp(8))
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
                        setBackgroundColor(Color.rgb(38,54,47)); gravity = Gravity.CENTER_VERTICAL
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
            backgroundTintList = ColorStateList.valueOf(Color.rgb(105,227,188)); setTextColor(Color.rgb(11,37,29))
            contentDescription = "保存待命时长"
            setOnClickListener {
                try {
                    val value = if (follow.isChecked) CapabilityBridgeService.duration(context) else
                        StandbyDuration.parse(unlimited.isChecked, if (unlimited.isChecked) 0.0 else amount.text.toString().toDouble(), unit.selectedItemPosition == 1)
                    if (!CapabilityBridgeService.saveDuration(context,value,follow.isChecked)) throw IllegalStateException("保存失败，请重试")
                    message.text = if (follow.isChecked) "已保存：跟随电脑，连接后确认同步" else if (value == 0L) "已保存：不限时" else "已保存：限时从本次保存开始计时"
                } catch (_: NumberFormatException) { message.text = "请输入有效时长" }
                catch (e: IllegalArgumentException) { message.text = e.message }
                catch (e: IllegalStateException) { message.text = e.message }
            }
        }
        addView(save,LayoutParams(-1,dp(44)).apply { topMargin = dp(8) })
        addView(message)
        addView(label("跟随电脑：由仪表盘统一设置，连接后同步剩余时间；未连上前保留本地期限。独立模式默认30分钟，保存立即生效；未开启时只保存。重连不续期。"),LayoutParams(-1,-2).apply { topMargin = dp(6) })
    }
}
