package com.example.heart_radio

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.*
import android.view.WindowManager
import android.widget.*

/** Explicit experimental entry. Opening it alone never starts a transport or sensor. */
class RfcommProbeActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var status: TextView
    private lateinit var heartStatus: TextView
    private lateinit var toggle: Button
    private val tick = object : Runnable { override fun run() {
        status.text = RfcommProbeService.status
        heartStatus.text = RfcommProbeService.heartSummary()
        toggle.text = if (RfcommProbeService.running()) "停止蓝牙测试" else "开始蓝牙测试"
        toggle.isEnabled=!ConnectionService.running()
        if(ConnectionService.running())status.text="主页面正在管理连接；请先断开后再做独立测试"
        handler.postDelayed(this, 1000)
    } }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        window.setBackgroundDrawable(android.graphics.drawable.ColorDrawable(WatchUi.canvas))
        val scale = resources.displayMetrics.density
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding((12*scale).toInt(), (12*scale).toInt(), (12*scale).toInt(), (24*scale).toInt())
            setBackgroundColor(WatchUi.canvas)
        }
        fun label(value: String, size: Float = 14f) = TextView(this).apply { text=value; textSize=size; setTextColor(Color.WHITE) }
        layout.addView(label("蓝牙双向测试", 18f))
        status = label("")
        layout.addView(status)
        heartStatus=label("心率尚未开始",14f)
        layout.addView(heartStatus)
        layout.addView(label("数值按接收时间显示，真实采样时间未验证。",12f))
        toggle = Button(this).apply {
            textSize=14f; isAllCaps=false; minHeight=(48*scale).toInt()
            setOnClickListener {
                if(ConnectionService.running())return@setOnClickListener
                if (RfcommProbeService.running()) stopService(Intent(this@RfcommProbeActivity, RfcommProbeService::class.java))
                else if (Build.VERSION.SDK_INT >= 31 && !Probe.bluetoothAllowed(this@RfcommProbeActivity))
                    requestPermissions(arrayOf("android.permission.BLUETOOTH_CONNECT", "android.permission.BLUETOOTH_ADVERTISE"), 21)
                else startForegroundService(Intent(this@RfcommProbeActivity, RfcommProbeService::class.java))
            }
        }
        layout.addView(toggle)
        layout.addView(label("提醒震动档位",14f))
        val strengthNames=arrayOf("light","standard","strong")
        val preferences=getSharedPreferences("reminder-settings",MODE_PRIVATE)
        layout.addView(Spinner(this).apply {
            adapter=ArrayAdapter(this@RfcommProbeActivity,android.R.layout.simple_spinner_dropdown_item,arrayOf("轻","标准","强"))
            setSelection(strengthNames.indexOf(preferences.getString("strength","standard")).coerceAtLeast(0))
            onItemSelectedListener=object:android.widget.AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent:android.widget.AdapterView<*>?,view:android.view.View?,position:Int,id:Long) {
                    preferences.edit().putString("strength",strengthNames[position]).apply()
                }
                override fun onNothingSelected(parent:android.widget.AdapterView<*>?) { }
            }
        },LinearLayout.LayoutParams(-1,(48*scale).toInt()))
        layout.addView(Button(this).apply {
            text="对比三档震动";textSize=14f;isAllCaps=false;minHeight=(48*scale).toInt()
            setOnClickListener { if(RfcommProbeService.running())RfcommProbeService.compareVibration()
                else Toast.makeText(this@RfcommProbeActivity,"请先开始蓝牙测试",Toast.LENGTH_SHORT).show() }
        })
        layout.addView(label("轻→标准→强；每档两次，间隔0.5秒；档位之间停2秒。",12f))
        layout.addView(label("独立测试通道，最长60分钟。仅接受已配置、已配对电脑。电脑可按需开始心率、读取状态和发送提醒。返回表盘继续测试，停止测试只释放连接；心率已统一到主页面，需单独停止。提醒受 Agent 交互开关控制。", 12f))
        setContentView(ScrollView(this).apply { isVerticalFadingEdgeEnabled=false; addView(layout) })
        // The maintenance flag is used only for the explicitly requested USB test session.
        if (intent.getBooleanExtra("start_test", false) && !ConnectionService.running() && !RfcommProbeService.running())
            startForegroundService(Intent(this, RfcommProbeService::class.java))
    }
    override fun onResume() { super.onResume(); handler.post(tick) }
    override fun onPause() { handler.removeCallbacks(tick); super.onPause() }
}
