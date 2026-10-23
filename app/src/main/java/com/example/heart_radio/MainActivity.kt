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
    private lateinit var connectionToggleButton: Button
    private lateinit var connectionState: TextView
    private lateinit var connectionDetail: TextView
    private lateinit var heartState: TextView
    private lateinit var pager: WatchPager
    private var modernBackCallback: android.window.OnBackInvokedCallback? = null
    private lateinit var heartToggleButton: Button
    private lateinit var standbyToggleButton: Button
    private lateinit var standbyStatus: TextView
    private lateinit var reminderPolicyView:ReminderPolicyView
    private lateinit var recentRemindersButton:Button
    private var heartControlBusyUntil = 0L
    private var standbyControlBusyUntil = 0L
    private var bleDetail: TextView? = null
    private var wifiDetail: TextView? = null
    private val accent = WatchUi.accent
    private val muted = WatchUi.muted
    private val canvasColor = WatchUi.canvas
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
        text = label; WatchUi.action(this, primary); textSize = 13f
        setOnClickListener { action() }
    }
    private fun entry(label: String, action: () -> Unit) = button(label, action = action).apply {
        gravity = Gravity.CENTER_VERTICAL or Gravity.START
        setPadding(0, dp(6), 0, dp(6))
        background = RippleDrawable(ColorStateList.valueOf(Color.argb(45, 255, 82, 104)), shape(canvasColor), shape(Color.WHITE))
    }
    private fun LinearLayout.addSpaced(child: View, gap: Int = 6) {
        addView(child, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(gap) })
    }
    private fun section(parent: LinearLayout, title: String, body: View) {
        body.visibility = View.GONE
        val toggle = entry("$title  ＋") {
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
        window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        window.setBackgroundDrawable(android.graphics.drawable.ColorDrawable(canvasColor))
        val names = listOf("心率", "常用开关", "使用设置")
        val root = WatchPageLayout(this, names.size)
        pager = WatchPager(this)
        fun page(title: String): Pair<ScrollView, LinearLayout> {
            val content = column().apply {
                setBackgroundColor(canvasColor); setPadding(dp(10), dp(6), dp(10), dp(18))
                addView(text(22f).apply { text = title; typeface = Typeface.DEFAULT_BOLD })
            }
            val scroll = ScrollView(this).apply {
                setBackgroundColor(canvasColor); isFillViewport = true
                isVerticalFadingEdgeEnabled = false; overScrollMode = View.OVER_SCROLL_NEVER
                isHorizontalScrollBarEnabled = false; isVerticalScrollBarEnabled = false
                addView(content, FrameLayout.LayoutParams(-1, -2))
            }
            pager.addPage(scroll)
            return scroll to content
        }
        val (homeScroll, home) = page("HeartRadio")
        home.addSpaced(text(12f, accent).apply { text = "最近心率" }, 12)
        val valueRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.BOTTOM; isBaselineAligned = true }
        bpm = text(42f).apply { typeface = Typeface.create("sans-serif-light", Typeface.NORMAL) }
        valueRow.addView(bpm)
        valueRow.addView(text(14f, muted).apply { text = "bpm" }, LinearLayout.LayoutParams(-2, -2).apply { marginStart = dp(6) })
        home.addSpaced(valueRow, 1)
        status = text(11f, muted).apply { maxLines = 2; ellipsize = android.text.TextUtils.TruncateAt.END }
        home.addSpaced(status, 3)
        receipt = text(10f, muted)
        sourceNote = text(11f, muted)
        permissionButton = button("申请身体传感器权限", primary = true) {
            Probe.log(this, "permission_request"); requestPermissions(arrayOf(Probe.permission()), 10)
        }
        home.addSpaced(permissionButton, 8)
        connectionState = text(11f, muted).apply { maxLines = 2; ellipsize = android.text.TextUtils.TruncateAt.END }
        home.addSpaced(connectionState, 10)
        recentRemindersButton = entry("最近提醒  ›") { startActivity(Intent(this, ReminderInboxActivity::class.java)) }
        home.addSpaced(recentRemindersButton, 8)
        home.addSpaced(entry("常用开关  ›") { pager.showPage(1) }, 0)

        val (_, controls) = page("常用开关")
        controls.addSpaced(text(12f, accent).apply { text = "电脑连接" }, 14)
        connectionToggleButton = button("开启连接") {
            if (ConnectionService.running()) ConnectionService.stop(this) else ConnectionService.start(this)
            handler.postDelayed({ render() }, 500)
        }.apply { ServiceToggleStyle.configure(this) }
        controls.addSpaced(connectionToggleButton, 6)
        connectionDetail = text(11f, muted)
        controls.addSpaced(connectionDetail, 5)
        controls.addSpaced(text(12f, accent).apply { text = "连续心率" }, 18)
        heartToggleButton = button("开启心率广播") {
            heartControlBusyUntil = SystemClock.uptimeMillis() + 500
            HeartBroadcast.set(if (HeartBroadcast.needsRepair()) false else !HeartBroadcast.desired())
            render(); handler.postDelayed({ render() }, 500)
        }.apply { ServiceToggleStyle.configure(this) }
        controls.addSpaced(heartToggleButton, 6)
        heartState = text(11f, muted)
        controls.addSpaced(heartState, 5)
        controls.addSpaced(text(12f, accent).apply { text = "读取与提醒" }, 18)
        standbyToggleButton = button("开启交互") {
            standbyControlBusyUntil = SystemClock.uptimeMillis() + 500
            AgentInteraction.set(this, if (AgentInteraction.needsRepair()) false else !AgentInteraction.enabled())
            render(); handler.postDelayed({ render() }, 500)
        }.apply { ServiceToggleStyle.configure(this) }
        controls.addSpaced(standbyToggleButton, 6)
        standbyStatus = text(11f, muted)
        controls.addSpaced(standbyStatus, 5)
        controls.addSpaced(entry("使用设置  ›") { pager.showPage(2) }, 12)

        val (_, settings) = page("使用设置")
        section(settings, "交互时长", StandbyDurationView(this))
        reminderPolicyView = ReminderPolicyView(this)
        section(settings, "提醒效果限制", reminderPolicyView)
        val connectionSettings = column()
        wifiDetail = text(11f, muted).also { connectionSettings.addSpaced(it, 4) }
        connectionSettings.addSpaced(button("选择连接方式与优先级") {
            android.app.AlertDialog.Builder(this).setTitle("连接方式（会保存）")
                .setItems(TransportSelection.modes.map { TransportSelection.label(it) }.toTypedArray()) { _, which ->
                    ConnectionService.start(this, TransportSelection.modes[which])
                }.setNegativeButton("取消", null).show()
        }, 4)
        connectionSettings.addSpaced(button("更换电脑连接码") { connectWifi(true) }, 4)
        section(settings, "连接设置", connectionSettings)
        val diagnostics = column()
        diagnostics.addSpaced(button("公开接口短时测试") { startActivity(Intent(this, CapabilityActivity::class.java)) }, 0)
        diagnostics.addSpaced(button("旧蓝牙独立测试") { startActivity(Intent(this, RfcommProbeActivity::class.java)) }, 6)
        diagnostics.addSpaced(button("旧 BLE 连接诊断（会采集）") { connectComputer() }, 6)
        diagnostics.addSpaced(button("旧 BLE 广播诊断（会采集）") {
            if (ConnectionService.running() || Probe.state.running) Probe.bleStatus = "请先关闭主页面连接和心率，再做旧BLE诊断"
            else if (!Probe.allowed(this)) Probe.bleStatus = "请先授予身体传感器权限"
            else if (!Probe.bluetoothAllowed(this)) requestPermissions(arrayOf("android.permission.BLUETOOTH_CONNECT", "android.permission.BLUETOOTH_ADVERTISE"), 11)
            else try { startForegroundService(Intent(this, CaptureService::class.java).setAction("BLE_START")) }
            catch (e: Exception) { Probe.bleStatus = "蓝牙启动失败：${e.message}" }
            render()
        }, 6)
        bleDetail = text(11f, muted).also { diagnostics.addSpaced(it, 6) }
        counts = text(11f, muted)
        detail = text(12f)
        section(diagnostics, "采集详情", column().apply { addSpaced(counts, 0); addSpaced(sourceNote, 8); addSpaced(detail, 8) })
        inventory = text(11f)
        section(diagnostics, "传感器清单", inventory)
        section(settings, "诊断与测试", diagnostics)
        section(settings, "使用说明", text(12f, muted).apply {
            text = "版本 ${packageManager.getPackageInfo(packageName, 0).versionName}\n\n左右滑动切换心率、常用开关和使用设置；每页可以上下滚动。向右返回前一页，在心率首页向右返回系统。\n\n提醒列表及已确认详情通过右划返回。未确认详情的“知道了”才会确认消息，右划不会确认。\n\n连接、心率广播和Agent交互独立控制。返回系统继续运行，不会关闭广播或重置交互期限。交互到期会取消单次任务并保留基础连接。\n\n超过30秒没有有效新心率时显示横线；时间按事件接收记录，原始采样时间未验证。旧诊断会启动额外测试采集。"
        })
        root.addView(pager, LinearLayout.LayoutParams(-1, -1))
        fun indicate(index: Int) {
            root.selectedPage=index
            pager.contentDescription = "第${index + 1}页：${names[index]}，共${names.size}页"
        }
        pager.onPageChanged = { index -> indicate(index); updateModernBack() }
        setContentView(WatchBackLayout(this, back = { navigateBack() }, enabled = { pager.currentPage == 0 }).apply {
            addView(root, FrameLayout.LayoutParams(-1, -1))
        })
        pager.post { pager.showPage(savedInstanceState?.getInt("watch_page", 0) ?: 0, false) }
        homeScroll.post {
            Probe.log(this, "ui_geometry", mapOf("widthPx" to homeScroll.width, "heightPx" to homeScroll.height,
                "density" to resources.displayMetrics.density, "fontScale" to resources.configuration.fontScale))
        }
        Probe.log(this, "activity_create")
        refreshInventory()
    }
    override fun onSaveInstanceState(outState: Bundle) {
        outState.putInt("watch_page", pager.currentPage)
        super.onSaveInstanceState(outState)
    }
    private fun navigateBack() {
        if (::pager.isInitialized && pager.currentPage > 0) pager.showPage(pager.currentPage - 1)
        else finish()
    }
    private fun updateModernBack() {
        if (Build.VERSION.SDK_INT < 33) return
        if (pager.currentPage > 0 && modernBackCallback == null) {
            modernBackCallback = android.window.OnBackInvokedCallback { navigateBack() }.also {
                onBackInvokedDispatcher.registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT, it)
            }
        } else if (pager.currentPage == 0) {
            modernBackCallback?.let { onBackInvokedDispatcher.unregisterOnBackInvokedCallback(it) }
            modernBackCallback = null
        }
    }
    // The API-27 watch dispatches Back here; API-33+ uses the callback above.
    @android.annotation.SuppressLint("GestureBackNavigation")
    override fun onBackPressed() = navigateBack()
    private fun refreshInventory() {
        // Preserve full raw inventory in the log; use short rows on the watch.
        Probe.inventory(this)
        val sensors = Probe.manager(this).getSensorList(android.hardware.Sensor.TYPE_ALL)
        inventory.text = "App 可见 ${sensors.size} 个\n\n" + sensors.joinToString("\n\n") { "${it.name}\n类型 ${it.type}" }
    }
    private fun connectComputer() {
        if(ConnectionService.running()||Probe.state.running){Probe.bleStatus="请先关闭主页面连接和心率，再做旧BLE诊断";render();return}
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
        val saved = getSharedPreferences("wifi", MODE_PRIVATE).getString("connection_code", "") ?: ""
        if (!editCode && saved.isNotBlank()) {
            try {
                WifiConfig.parse(saved)
                ConnectionService.start(this,"wifi")
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
            .setPositiveButton(if(editCode)"保存" else "连接") { _, _ ->
                try {
                    val code = input.text.toString().trim()
                    WifiConfig.parse(code)
                    getSharedPreferences("wifi",MODE_PRIVATE).edit().putString("connection_code",code).apply()
                    if(!editCode)ConnectionService.start(this,"wifi")
                } catch (e: Exception) { Probe.wifiStatus = "Wi-Fi 启动失败：${e.message}" }
                render()
            }.setNegativeButton("取消", null).show()
    }
    private fun accuracy(n: Int) = when (n) { -1 -> "无接触 (-1)"; 0 -> "不可靠 (0)"; 1 -> "低 (1)"; 2 -> "中 (2)"; 3 -> "高 (3)"; else -> "尚无状态" }
    private fun render() {
        val st = Probe.state; val now = SystemClock.elapsedRealtimeNanos(); val allowed = Probe.allowed(this)
        reminderPolicyView.refresh()
        val unread=ReminderEngine.unreadCount()
        recentRemindersButton.show(if(unread>0)"最近提醒 · ${unread}未确认  ›" else "最近提醒  ›")
        val sensor = Probe.sensor(this)
        val current = st.recentValue(now)?.takeIf { allowed && sensor != null }
        bpm.show(current?.let { "%.0f".format(Locale.US, it) } ?: "—")
        receipt.show(if (st.lastDeliveryNs == 0L) "尚未收到事件" else "上次回调：${seconds(now, st.lastDeliveryNs)}前")
        status.show(st.status(now, allowed, sensor != null))
        if(!st.running&&HeartSensorHub.owners()>0)status.show("一次性心率测量中 · 广播关闭")
        sourceNote.show("时间按手表接收近似记录\n心率广播：${HeartBroadcast.state().optString("syncStatus")}")
        bleDetail?.show(Probe.bleStatus)
        wifiDetail?.show(Probe.wifiStatus)
        permissionButton.visibility = if (allowed) View.GONE else View.VISIBLE
        heartToggleButton.show(if(HeartBroadcast.needsRepair())"重试关闭广播" else if(HeartBroadcast.desired())"关闭心率广播" else "开启心率广播")
        heartToggleButton.isSelected = HeartBroadcast.desired()
        heartToggleButton.isEnabled = (HeartBroadcast.desired() || HeartBroadcast.needsRepair() || allowed) && SystemClock.uptimeMillis() >= heartControlBusyUntil
        heartToggleButton.alpha = if (heartToggleButton.isEnabled) 1f else 0.45f
        connectionToggleButton.show(if(ConnectionService.running())"断开连接" else "开启连接")
        connectionToggleButton.contentDescription=if(ConnectionService.running())"断开手表与电脑连接" else "开启手表与电脑连接"
        connectionToggleButton.isSelected=ConnectionService.running()
        val connectionText=if(ProjectSession.suspended)"未连接满10分钟，全部服务已停止\n点开启连接，恢复原先功能" else ConnectionService.status
        connectionState.show(connectionText)
        connectionDetail.show(connectionText)
        heartState.show(HeartBroadcast.state().optString("syncStatus"))
        val standbyRunning=AgentInteraction.enabled()
        standbyToggleButton.show(if(AgentInteraction.needsRepair())"重试关闭" else if(standbyRunning)"关闭交互" else "开启交互")
        standbyToggleButton.isSelected=standbyRunning
        standbyToggleButton.isEnabled=SystemClock.uptimeMillis()>=standbyControlBusyUntil
        val gate=AgentInteraction.state()
        val timing=if(standbyRunning&&!gate.isNull("remainingMs"))" · 剩余${gate.optLong("remainingMs")/1000}秒" else ""
        standbyStatus.show("${if(standbyRunning)"读取与提醒已允许$timing" else "读取与提醒已关闭"}\n${gate.optString("syncStatus")}${if(!ConnectionService.connected())" · 离线" else ""}")
        if(st.running&&!ConnectionService.connected())status.show("${HeartStream.diagnosticJson().optString("message")} · 未传输")
        counts.show("回调 ${st.callbacks}  ·  有效 ${st.validCount}")
        val last = if (st.lastWallMs == 0L) "—" else SimpleDateFormat("HH:mm:ss.SSS", Locale.US).format(Date(st.lastWallMs))
        detail.show("身体传感器权限\n${if (allowed) "已授权" else "未授权 / 被拒绝"}\n\n标准心率传感器\n${sensor?.name ?: "未找到"}\n\n监听结果\n${st.result}\n当前注册：${if (st.registered) "是" else "否"}\n\n精度 / 状态\n${accuracy(st.accuracy)}\n\n最近送达\n$last\n\n距原始事件时间戳\n${seconds(now, st.lastEventNs)}\n\n距事件送达\n${seconds(now, st.lastDeliveryNs)}\n\n距有效读数送达\n${seconds(now, st.lastValidDeliveryNs)}\n\n距有效读数源时间戳\n${seconds(now, st.lastValidNs)}\n\n屏幕\n${if (Probe.screen(this)) "交互亮屏" else "非交互 / 息屏"}\n\n日志\n${Probe.logFile?.name ?: "尚未采集"}${if (Probe.logError.isBlank()) "" else "\n${Probe.logError}"}")
    }
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        Probe.log(this, "permission_result", mapOf("granted" to Probe.allowed(this), "results" to grantResults.joinToString()))
        refreshInventory(); render()
    }
    override fun onResume() { super.onResume();ConnectionService.resume(this); Probe.log(this, "activity_resume"); handler.post(ticker) }
    override fun onPause() { Probe.log(this, "activity_pause"); handler.removeCallbacks(ticker); super.onPause() }
    override fun onStop() { Probe.log(this, "activity_stop"); super.onStop() }
    override fun onDestroy() {
        if (Build.VERSION.SDK_INT >= 33) modernBackCallback?.let { onBackInvokedDispatcher.unregisterOnBackInvokedCallback(it) }
        Probe.log(this, "activity_destroy"); super.onDestroy()
    }
}
