package com.example.heart_radio

import android.app.Activity
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.View
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/** Optional test screen; opening the normal heart-rate screen does not start the bridge. */
class CapabilityActivity : Activity() {
    private val canvasColor = Color.rgb(16, 22, 20)
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var status: TextView
    private lateinit var standbyToggle: Button
    private var controlBusyUntil = 0L
    private val refresh = object : Runnable {
        override fun run() {
            val message = CapabilityBridgeService.status + "\n\n定位权限：" +
                (if (checkSelfPermission("android.permission.ACCESS_FINE_LOCATION") == PackageManager.PERMISSION_GRANTED) "已授权" else "未授权")
            if (status.text.toString() != message) status.text = message
            val toggleText = if (CapabilityBridgeService.isRunning()) "关闭待命通道" else "开启待命通道"
            if (standbyToggle.text.toString() != toggleText) standbyToggle.text = toggleText
            standbyToggle.isSelected = CapabilityBridgeService.isRunning()
            standbyToggle.isEnabled = SystemClock.uptimeMillis() >= controlBusyUntil
            handler.postDelayed(this, 1000)
        }
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Match the main screen's software-rendering workaround on this API 27 watch.
        window.setBackgroundDrawable(ColorDrawable(canvasColor))
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(12, 16, 12, 24)
            setBackgroundColor(canvasColor)
        }
        fun label(value: String) = TextView(this).apply { text = value; textSize = 13f; setTextColor(Color.WHITE) }
        fun action(value: String, callback: () -> Unit) = Button(this).apply {
            text = value; isAllCaps = false; setOnClickListener { callback() }
        }.also { layout.addView(it) }
        layout.addView(label("公开接口短时测试\n\n电脑可请求清单、状态和一次测量。无请求时不监听新增传感器；传感器测试最多60秒，定位诊断最多180秒并记录卫星状态。待命默认30分钟，可在下方设置不限时或自定义时长。重连不重新计时，原心率采集独立运行。"))
        status = label(""); layout.addView(status)
        action("申请定位权限") { requestPermissions(arrayOf("android.permission.ACCESS_FINE_LOCATION", "android.permission.ACCESS_COARSE_LOCATION"), 20) }
        standbyToggle = action("开启待命通道") {
            controlBusyUntil = SystemClock.uptimeMillis() + 500
            standbyToggle.isEnabled = false
            if (CapabilityBridgeService.isRunning()) stopService(Intent(this, CapabilityBridgeService::class.java)) else begin()
        }
        ServiceToggleStyle.configure(standbyToggle)
        layout.addView(label("待命时长设置"))
        layout.addView(StandbyDurationView(this))
        setContentView(ScrollView(this).apply {
            // Redraw an opaque viewport, including newly exposed top/bottom strips.
            setBackgroundColor(canvasColor)
            isVerticalFadingEdgeEnabled = false
            isHorizontalFadingEdgeEnabled = false
            overScrollMode = View.OVER_SCROLL_NEVER
            isFillViewport = true
            isHorizontalScrollBarEnabled = false
            isVerticalScrollBarEnabled = true
            clipToPadding = false
            addView(layout, FrameLayout.LayoutParams(-1, -2))
        })
        // Used only by the explicitly invoked local ADB test script, after provisioning private config.
        if (intent.getBooleanExtra("start_bridge", false) && applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) begin()
    }
    private fun begin() {
        try { startForegroundService(Intent(this, CapabilityBridgeService::class.java)) }
        catch (e: Exception) { CapabilityBridgeService.status = "启动失败：${e.javaClass.simpleName}" }
    }
    override fun onResume() { super.onResume(); handler.post(refresh) }
    override fun onPause() { handler.removeCallbacks(refresh); super.onPause() }
}
