package com.example.heart_radio

import android.app.Activity
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.os.*
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.*
import java.text.SimpleDateFormat
import java.util.*

class MainActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var bpm: TextView
    private lateinit var status: TextView
    private lateinit var receipt: TextView
    private lateinit var sourceNote: TextView
    private lateinit var counts: TextView
    private lateinit var detail: TextView
    private lateinit var inventory: TextView
    private lateinit var permissionButton: Button
    private lateinit var heartToggleButton: Button
    private lateinit var standbyToggleButton: Button
    private lateinit var standbyStatus: TextView
    private var heartControlBusyUntil = 0L
    private var standbyControlBusyUntil = 0L
    private var bleDetail: TextView? = null
    private var wifiDetail: TextView? = null
    private val accent = Color.rgb(105, 227, 188)
    private val muted = Color.rgb(170, 190, 182)
    private val canvasColor = Color.rgb(16, 22, 20)
    private val ticker = object : Runnable { override fun run() { render(); handler.postDelayed(this, 1000) } }
    private fun dp(value: Int) = (value * resources.displayMetrics.density + 0.5f).toInt()
    private fun TextView.show(value: String) { if (text.toString() != value) text = value }
    private fun text(size: Float = 12f, color: Int = Color.WHITE) = TextView(this).apply {
        textSize = size; setTextColor(color); includeFontPadding = false
        setLineSpacing(dp(2).toFloat(), 1f)
    }
    private fun column() = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
    private fun shape(color: Int) = GradientDrawable().apply { setColor(color); cornerRadius = dp(10).toFloat() }
    private fun button(label: String, primary: Boolean = false, action: () -> Unit) = Button(this).apply {
        text = label; textSize = 12f; isAllCaps = false; includeFontPadding = false
        gravity = Gravity.CENTER; minimumWidth = 0; minWidth = 0
        minimumHeight = dp(44); minHeight = dp(44)
        setPadding(dp(4), dp(6), dp(4), dp(6))
        val enabledText = if (primary) Color.rgb(11, 37, 29) else Color.WHITE
        setTextColor(ColorStateList(arrayOf(intArrayOf(-android.R.attr.state_enabled), intArrayOf()), intArrayOf(muted, enabledText)))
        background = RippleDrawable(ColorStateList.valueOf(Color.argb(65, 255, 255, 255)),
            shape(if (primary) accent else Color.rgb(38, 54, 47)), null)
        setOnClickListener { action() }
    }
    private fun LinearLayout.addSpaced(child: View, gap: Int = 6) {
        addView(child, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(gap) })
    }
    private fun section(parent: LinearLayout, title: String, body: View) {
        body.visibility = View.GONE
        val toggle = button("$title  ＋") {
            val open = body.visibility != View.VISIBLE
            body.visibility = if (open) View.VISIBLE else View.GONE
            // Only the tapped section changes; live values never rebuild the scroll container.
            (body.tag as Button).text = "$title  ${if (open) "－" else "＋"}"
        }.apply { gravity = Gravity.CENTER_VERTICAL or Gravity.START; setPadding(dp(10), dp(6), dp(10), dp(6)) }
        body.tag = toggle
        parent.addSpaced(toggle, 8)
        parent.addSpaced(body, 6)
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Use the watch's full display; this does not keep it awake or change system settings.
        window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        window.setBackgroundDrawable(android.graphics.drawable.ColorDrawable(canvasColor))
        val layout = column().apply {
            setBackgroundColor(canvasColor)
            setPadding(dp(12), dp(12), dp(12), dp(24))
        }
        layout.addView(text(12f, muted).apply { text = "心率可行性测试"; gravity = Gravity.CENTER })
        layout.addSpaced(text(10f, muted).apply { text = "最近收到的心率"; gravity = Gravity.CENTER }, 4)
        bpm = text(28f, accent).apply { gravity = Gravity.CENTER; typeface = Typeface.DEFAULT_BOLD }
        layout.addSpaced(bpm, 4)
        receipt = text(10f, muted).apply { gravity = Gravity.CENTER }
        layout.addSpaced(receipt, 2)
        status = text(11f).apply { gravity = Gravity.CENTER }
        layout.addSpaced(status, 4)
        sourceNote = text(10f, muted).apply { gravity = Gravity.CENTER }
        layout.addSpaced(sourceNote, 3)
        permissionButton = button("申请身体传感器权限", primary = true) {
            Probe.log(this, "permission_request")
            requestPermissions(arrayOf(Probe.permission()), 10)
        }
        layout.addSpaced(permissionButton, 8)
        val actions = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        heartToggleButton = button("开启心率广播", primary = true) {
            heartControlBusyUntil = SystemClock.uptimeMillis() + 500
            if (Probe.state.running) {
                Probe.log(this, "stop_button")
                stopService(Intent(this, CaptureService::class.java))
            } else connectWifi()
            render()
            handler.postDelayed({ render() }, 500)
        }.apply { textSize = 11f; ServiceToggleStyle.configure(this) }
        standbyToggleButton = button("开启待命通道", primary = true) {
            standbyControlBusyUntil = SystemClock.uptimeMillis() + 500
            if (CapabilityBridgeService.isRunning()) stopService(Intent(this, CapabilityBridgeService::class.java))
            else try { startForegroundService(Intent(this, CapabilityBridgeService::class.java)) }
            catch (e: Exception) { CapabilityBridgeService.status = "启动失败：${e.javaClass.simpleName}" }
            render()
            handler.postDelayed({ render() }, 500)
        }.apply { textSize = 11f; ServiceToggleStyle.configure(this) }
        actions.addView(heartToggleButton, LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(3) })
        actions.addView(standbyToggleButton, LinearLayout.LayoutParams(0, -2, 1f).apply { marginStart = dp(3) })
        layout.addSpaced(actions, 6)
        standbyStatus = text(10f, muted).apply { gravity = Gravity.CENTER }
        layout.addSpaced(standbyStatus, 6)
        wifiDetail = text(10f, muted).apply { gravity = Gravity.CENTER }.also { layout.addSpaced(it, 4) }
        counts = text(11f, muted).apply { gravity = Gravity.CENTER }
        layout.addSpaced(counts, 8)
        layout.addSpaced(text(11f, muted).apply { text = "向上滑动查看详情 ↓"; gravity = Gravity.CENTER }, 5)
        layout.addSpaced(button("公开接口短时测试") { startActivity(Intent(this, CapabilityActivity::class.java)) }, 4)
        section(layout, "待命时长设置", StandbyDurationView(this))
        val connectionSettings = column()
        connectionSettings.addSpaced(button("更换电脑连接码") { connectWifi(true) }, 4)
        connectionSettings.addSpaced(button("连接电脑（蓝牙）") { connectComputer() }, 4)
        connectionSettings.addSpaced(button("旧广播模式诊断") {
            if (!Probe.allowed(this)) { Probe.bleStatus = "请先授予身体传感器权限" }
            else if (!Probe.bluetoothAllowed(this)) requestPermissions(arrayOf("android.permission.BLUETOOTH_CONNECT", "android.permission.BLUETOOTH_ADVERTISE"), 11)
            else try { startForegroundService(Intent(this, CaptureService::class.java).setAction("BLE_START")) }
            catch (e: Exception) { Probe.bleStatus = "蓝牙启动失败：${e.message}" }
            render()
        }, 4)
        val bluetoothDetail = text(11f, muted)
        connectionSettings.addSpaced(bluetoothDetail, 4)
        bleDetail = bluetoothDetail
        section(layout, "连接设置", connectionSettings)
        detail = text()
        section(layout, "采集详情", detail)
        inventory = text(11f)
        section(layout, "传感器清单", inventory)
        section(layout, "测试说明", text(12f, muted).apply {
            text = "独立开关版 ${packageManager.getPackageInfo(packageName, 0).versionName}\n\n时间使用手表收到事件的时刻作为近似测量时间。原始时间戳保留用于诊断。\n\n超过 30 秒无新回调、无接触、数据无效或停止时显示横线。30 秒是本项目显示规则，不是传感器保证周期。\n\n先运行电脑接收程序，再点开启心率广播。首次填写连接码后会自动寻找原电脑。关闭心率广播同时结束心率采集和原连接，不关闭待命通道。\n\n待命通道单独开启，需要电脑按需服务与已配置的通道。无请求时不监听新增传感器；默认30分钟，可在待命时长设置中选择不限时或自定义时间；保存立即更新当前期限，未开启时仅保存。关闭待命通道不停止心率。重新打开页面会显示实际状态，不自动开启或关闭服务。\n\n心率为 on-change；无更新不等于服务停止。App 不常亮，原心率不持有唤醒锁；按需测量仅短时使用。返回表盘仍按原期限运行。"
        })
        val scroll = ScrollView(this).apply {
            setBackgroundColor(canvasColor)
            isVerticalFadingEdgeEnabled = false
            isFillViewport = true; isHorizontalScrollBarEnabled = false
            isVerticalScrollBarEnabled = true; clipToPadding = false
            addView(layout, FrameLayout.LayoutParams(-1, -2))
        }
        setContentView(scroll)
        scroll.post {
            Probe.log(this, "ui_geometry", mapOf("widthPx" to scroll.width, "heightPx" to scroll.height,
                "density" to resources.displayMetrics.density, "fontScale" to resources.configuration.fontScale))
        }
        Probe.log(this, "activity_create")
        refreshInventory()
    }
    private fun refreshInventory() {
        // Preserve full raw inventory in the log; use short rows on the watch.
        Probe.inventory(this)
        val sensors = Probe.manager(this).getSensorList(android.hardware.Sensor.TYPE_ALL)
        inventory.text = "App 可见 ${sensors.size} 个\n\n" + sensors.joinToString("\n\n") { "${it.name}\n类型 ${it.type}" }
    }
    private fun connectComputer() {
        if (!Probe.allowed(this)) { Probe.bleStatus = "请先授予身体传感器权限"; render(); return }
        if (!Probe.bluetoothAllowed(this)) { requestPermissions(arrayOf("android.permission.BLUETOOTH_CONNECT", "android.permission.BLUETOOTH_ADVERTISE"), 11); return }
        val input = EditText(this).apply {
            setSingleLine(true); textSize = 12f
            setText(getSharedPreferences("ble", MODE_PRIVATE).getString("pc_address", ""))
            hint = "电脑蓝牙地址"
        }
        android.app.AlertDialog.Builder(this).setTitle("已配对电脑地址").setView(input)
            .setPositiveButton("连接") { _, _ ->
                val address = input.text.toString().trim().uppercase(Locale.US)
                if (!android.bluetooth.BluetoothAdapter.checkBluetoothAddress(address)) Probe.bleStatus = "电脑地址格式错误"
                else try { startForegroundService(Intent(this, CaptureService::class.java).setAction("BLE_CLIENT_START").putExtra("pc_address", address)) }
                catch (e: Exception) { Probe.bleStatus = "连接失败：${e.message}" }
                render()
            }.setNegativeButton("取消", null).show()
    }
    private fun seconds(now: Long, then: Long) = if (then <= 0L) "—" else "%.1f 秒".format(Locale.US, (now - then).coerceAtLeast(0) / 1e9)
    private fun connectWifi(editCode: Boolean = false) {
        if (!Probe.allowed(this)) { Probe.wifiStatus = "请先授予身体传感器权限"; render(); return }
        val saved = getSharedPreferences("wifi", MODE_PRIVATE).getString("connection_code", "") ?: ""
        if (!editCode && saved.isNotBlank()) {
            try {
                WifiConfig.parse(saved)
                startForegroundService(Intent(this, CaptureService::class.java).setAction("WIFI_START"))
                return
            } catch (e: Exception) { Probe.wifiStatus = "请检查已保存的连接码：${e.message}" }
        }
        val input = EditText(this).apply {
            textSize = 10f; maxLines = 4
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            setText(getSharedPreferences("wifi", MODE_PRIVATE).getString("connection_code", ""))
            hint = "电脑生成的连接码"
        }
        android.app.AlertDialog.Builder(this).setTitle("局域网电脑连接码").setView(input)
            .setPositiveButton("连接") { _, _ ->
                try {
                    val code = input.text.toString().trim()
                    WifiConfig.parse(code)
                    startForegroundService(Intent(this, CaptureService::class.java).setAction("WIFI_START").putExtra("connection_code", code))
                } catch (e: Exception) { Probe.wifiStatus = "Wi-Fi 启动失败：${e.message}" }
                render()
            }.setNegativeButton("取消", null).show()
    }
    private fun accuracy(n: Int) = when (n) { -1 -> "无接触 (-1)"; 0 -> "不可靠 (0)"; 1 -> "低 (1)"; 2 -> "中 (2)"; 3 -> "高 (3)"; else -> "尚无状态" }
    private fun render() {
        val st = Probe.state; val now = SystemClock.elapsedRealtimeNanos(); val allowed = Probe.allowed(this)
        val sensor = Probe.sensor(this)
        val current = st.recentValue(now)?.takeIf { allowed && sensor != null }
        bpm.show("${current?.let { "%.0f".format(Locale.US, it) } ?: "—"} bpm")
        receipt.show(if (st.lastDeliveryNs == 0L) "尚未收到事件" else "上次回调：${seconds(now, st.lastDeliveryNs)}前")
        status.show(st.status(now, allowed, sensor != null))
        sourceNote.show("时间按手表接收近似记录")
        bleDetail?.show(Probe.bleStatus)
        wifiDetail?.show(Probe.wifiStatus)
        permissionButton.visibility = if (allowed) View.GONE else View.VISIBLE
        heartToggleButton.show(if (st.running) "关闭心率广播" else "开启心率广播")
        heartToggleButton.isSelected = st.running
        heartToggleButton.isEnabled = (st.running || allowed) && SystemClock.uptimeMillis() >= heartControlBusyUntil
        heartToggleButton.alpha = if (heartToggleButton.isEnabled) 1f else 0.45f
        val standbyRunning = CapabilityBridgeService.isRunning()
        standbyToggleButton.show(if (standbyRunning) "关闭待命通道" else "开启待命通道")
        standbyToggleButton.isSelected = standbyRunning
        standbyToggleButton.isEnabled = SystemClock.uptimeMillis() >= standbyControlBusyUntil
        standbyToggleButton.alpha = if (standbyToggleButton.isEnabled) 1f else 0.45f
        val bridgeState = CapabilityBridgeService.state()
        val remainingMs = bridgeState.optLong("leaseRemainingMs", 0)
        val remainingSeconds = (remainingMs + 999) / 1000
        val timing = if (standbyRunning)
            if (bridgeState.optBoolean("leaseUnlimited")) "\n本次不限时" else "\n剩余 ${remainingSeconds / 60}分${remainingSeconds % 60}秒"
            else if (CapabilityBridgeService.duration(this) == 0L) "\n下次开启：不限时" else "\n下次开启：${CapabilityBridgeService.duration(this) / 60_000.0}分钟"
        val syncing = if (CapabilityBridgeService.followsComputer(this))
            if (standbyRunning && bridgeState.optString("durationSyncRevision").isNotEmpty()) "\n时长跟随电脑（已同步）"
            else "\n时长跟随电脑（连接后同步）" else ""
        standbyStatus.show("待命：${CapabilityBridgeService.status}" + timing + syncing)
        counts.show("回调 ${st.callbacks}  ·  有效 ${st.validCount}")
        val last = if (st.lastWallMs == 0L) "—" else SimpleDateFormat("HH:mm:ss.SSS", Locale.US).format(Date(st.lastWallMs))
        detail.show("身体传感器权限\n${if (allowed) "已授权" else "未授权 / 被拒绝"}\n\n标准心率传感器\n${sensor?.name ?: "未找到"}\n\n监听结果\n${st.result}\n当前注册：${if (st.registered) "是" else "否"}\n\n精度 / 状态\n${accuracy(st.accuracy)}\n\n最近送达\n$last\n\n距原始事件时间戳\n${seconds(now, st.lastEventNs)}\n\n距事件送达\n${seconds(now, st.lastDeliveryNs)}\n\n距有效读数送达\n${seconds(now, st.lastValidDeliveryNs)}\n\n距有效读数源时间戳\n${seconds(now, st.lastValidNs)}\n\n屏幕\n${if (Probe.screen(this)) "交互亮屏" else "非交互 / 息屏"}\n\n日志\n${Probe.logFile?.name ?: "尚未采集"}${if (Probe.logError.isBlank()) "" else "\n${Probe.logError}"}")
    }
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        Probe.log(this, "permission_result", mapOf("granted" to Probe.allowed(this), "results" to grantResults.joinToString()))
        refreshInventory(); render()
    }
    override fun onResume() { super.onResume(); Probe.log(this, "activity_resume"); handler.post(ticker) }
    override fun onPause() { Probe.log(this, "activity_pause"); handler.removeCallbacks(ticker); super.onPause() }
    override fun onStop() { Probe.log(this, "activity_stop"); super.onStop() }
    override fun onDestroy() { Probe.log(this, "activity_destroy"); super.onDestroy() }
}
