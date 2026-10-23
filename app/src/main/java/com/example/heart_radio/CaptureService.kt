package com.example.heart_radio

import android.app.*
import android.content.*
import android.hardware.*
import android.os.*

class CaptureService : Service(), SensorEventListener {
    private val handler = Handler(Looper.getMainLooper())
    private var receiverAdded = false
    private var ble: BleTransport? = null
    private var lastDiagnosticMs = 0L
    private var hasCaptureSession=false
    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, i: Intent) {
            if (i.action == Intent.ACTION_POWER_CONNECTED || i.action == Intent.ACTION_POWER_DISCONNECTED)
                CaptureDiagnostics.record(c, i.action ?: "power")
            else Probe.log(c, "screen", mapOf("action" to i.action))
        }
    }
    private val heartbeat = object : Runnable {
        override fun run() {
            if (!Probe.state.running) return
            if (!Probe.allowed(this@CaptureService)) { finishCapture("permission_lost"); return }
            Probe.log(this@CaptureService, "heartbeat", mapOf("callbacks" to Probe.state.callbacks, "validCount" to Probe.state.validCount))
            val nowMs = SystemClock.elapsedRealtime()
            if (nowMs - lastDiagnosticMs >= 60_000) {
                CaptureDiagnostics.record(this@CaptureService, "periodic")
                lastDiagnosticMs = nowMs
            }
            HeartStream.publish(2)
            if (ble !is BleDuplex) ble?.publish(2) // Duplex has its own 5-second link status, never a sensor sample.
            handler.postDelayed(this, 15_000) // No wake lock/alarm: may be delayed by sleep.
        }
    }
    override fun onBind(intent: Intent?) = null
    override fun onCreate() {
        super.onCreate()
        Probe.log(this, "service_create")
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if(ProjectSession.suspended){stopSelf(startId);return START_NOT_STICKY}
        if (intent?.action == "STOP") { finishCapture("notification_stop"); return START_NOT_STICKY }
        if(intent?.action=="HEART_SYNC_START" && !HeartBroadcast.acceptsStart(intent.getStringExtra("broadcastVersion")?:"")){
            if(!Probe.state.running)stopSelf(startId)
            return START_NOT_STICKY
        }
        if(intent?.action!="HEART_SYNC_START" && !Probe.state.running && !HeartBroadcast.externalStart()){
            stopSelf(startId);return START_NOT_STICKY
        }
        val bluetoothStart = intent?.action == "BLE_START" || intent?.action == "BLE_CLIENT_START"
        val wifiStart = intent?.action == "WIFI_START"
        if (bluetoothStart && Probe.state.running) { startBle(intent); return START_NOT_STICKY }
        if (wifiStart && Probe.state.running) { startWifi(intent); return START_NOT_STICKY }
        if (Probe.state.running) return START_NOT_STICKY
        Probe.startLog(this)
        hasCaptureSession=true
        val startedNs = SystemClock.elapsedRealtimeNanos()
        Probe.state.reset(startedNs)
        HeartStream.lastEvent = null
        HeartStream.diagnostics.begin(java.util.UUID.randomUUID().toString(), startedNs)
        val battery=registerReceiver(null,IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        HeartStream.startContext=org.json.JSONObject().put("screenInteractive",Probe.screen(this))
            .put("plugged",battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED,-1)).put("requestedPeriodUs",1_000_000)
        Probe.log(this, "session_start", mapOf("permission" to Probe.allowed(this), "staleRuleSeconds" to 30,
            "sdk" to Build.VERSION.SDK_INT, "model" to Build.MODEL, "requestedPeriodUs" to 1_000_000,
            "maxReportLatencyUs" to 0, "wakeLock" to false, "diagnosticSchema" to 3,
            "appVersion" to packageManager.getPackageInfo(packageName, 0).versionName, "callbackThread" to "main", "recordTimeBasis" to "watch_receipt_approximation",
            "displayPolicy" to "recent receipt within 30 seconds; source sample age unverified",
            "uptimeMs" to SystemClock.uptimeMillis(),
            "timestampDifferenceMeaning" to "arrival minus raw event timestamp; not proven latency"))
        CaptureDiagnostics.record(this, "session_start")
        lastDiagnosticMs = SystemClock.elapsedRealtime()
        try {
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel("capture", "本地心率采集", NotificationManager.IMPORTANCE_LOW))
            val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val stop = PendingIntent.getService(this, 1, Intent(this, CaptureService::class.java).setAction("STOP"), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val notification = Notification.Builder(this, "capture").setSmallIcon(android.R.drawable.ic_menu_info_details)
                .setContentTitle("正在测试本地心率").setContentText("本地采集 · 点停止结束")
                .setContentIntent(open).setOngoing(true).addAction(Notification.Action.Builder(null, "停止", stop).build()).build()
            if (Build.VERSION.SDK_INT >= 34) startForeground(1, notification, android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_HEALTH or
                (if (bluetoothStart) android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE else 0))
            else startForeground(1, notification)
            if (!Probe.allowed(this)) { fail("无身体传感器权限"); return START_NOT_STICKY }
            val sensor = Probe.sensor(this)
            if (sensor == null) { fail("App 不可见标准心率传感器"); return START_NOT_STICKY }
            Probe.log(this, "sensor_selected", mapOf("name" to sensor.name, "type" to sensor.type,
                "vendor" to sensor.vendor, "reportingMode" to sensor.reportingMode, "wakeUp" to sensor.isWakeUpSensor,
                "minDelayUs" to sensor.minDelay, "maxDelayUs" to sensor.maxDelay, "fifoMax" to sensor.fifoMaxEventCount))
            val filter = IntentFilter().apply {
                addAction(Intent.ACTION_SCREEN_ON); addAction(Intent.ACTION_SCREEN_OFF)
                addAction(Intent.ACTION_POWER_CONNECTED); addAction(Intent.ACTION_POWER_DISCONNECTED)
            }
            if (Build.VERSION.SDK_INT >= 33) registerReceiver(screenReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
            else registerReceiver(screenReceiver, filter)
            receiverAdded = true
            val ok = HeartSensorHub.acquire(this,"continuous",this)
            ProjectSession.monitor(this)
            Probe.state.registered = ok
            if (ok) HeartStream.diagnostics.registered()
            Probe.state.result = if (ok) "注册成功 (true)" else "失败：registerListener 返回 false"
            Probe.log(this, "registration", mapOf("success" to ok, "result" to Probe.state.result))
            if (!ok) finishCapture("registration_false") else { handler.post(heartbeat); if (bluetoothStart) startBle(intent); if (wifiStart) startWifi(intent) }
        } catch (e: Exception) { fail("${e.javaClass.simpleName}: ${e.message}") }
        return START_NOT_STICKY
    }
    private fun startBle(intent: Intent?) {
        if (!Probe.bluetoothAllowed(this)) { Probe.bleStatus = "蓝牙权限未授权"; return }
        // On newer Android restart through a fresh foreground start so both service types are declared.
        if (Build.VERSION.SDK_INT >= 34) {
            val nm = getSystemService(NotificationManager::class.java)
            nm.activeNotifications.firstOrNull { it.id == 1 }?.notification?.let {
                startForeground(1, it, android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_HEALTH or android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
            }
        }
        ble?.close(false)
        if (intent?.action == "BLE_CLIENT_START") {
            val preferences = getSharedPreferences("ble", MODE_PRIVATE)
            val address = (intent.getStringExtra("pc_address") ?: preferences.getString("pc_address", "") ?: "").uppercase(java.util.Locale.US)
            if (!android.bluetooth.BluetoothAdapter.checkBluetoothAddress(address)) { Probe.bleStatus = "请填写已配对电脑地址"; ble = null; return }
            preferences.edit().putString("pc_address", address).apply()
            ble = BleDuplex(this, address)
        } else ble = BlePeripheral(this)
        ble?.start()
    }
    private fun startWifi(intent: Intent?) {
        intent?.getStringExtra("connection_code")?.let {
            getSharedPreferences("wifi",MODE_PRIVATE).edit().putString("connection_code",it).apply()
        }
        ConnectionService.start(this,"wifi")
    }
    private fun fail(reason: String) {
        HeartStream.diagnostics.failed(reason,SystemClock.elapsedRealtimeNanos())
        Probe.state.result = "失败：$reason"
        Probe.log(this, "failure", mapOf("reason" to reason))
        finishCapture("failure")
    }
    override fun onSensorChanged(event: SensorEvent) {
        val st = Probe.state
        if (!st.running || !st.registered || event.sensor.type != Sensor.TYPE_HEART_RATE) return
        val arrival = SystemClock.elapsedRealtimeNanos()
        val uptime = SystemClock.uptimeMillis()
        val previousArrival = st.lastDeliveryNs
        val previousEvent = st.lastEventNs
        val bpm = event.values.firstOrNull() ?: Float.NaN
        val valid = st.accept(bpm, event.accuracy, event.timestamp, arrival, System.currentTimeMillis())
        Probe.log(this, "event", mapOf("bpm" to if (bpm.isFinite()) bpm else null, "rawValue" to bpm.toString(),
            "accuracy" to event.accuracy, "eventNs" to event.timestamp, "arrivalNs" to arrival,
            "arrivalUptimeMs" to uptime,
            "arrivalIntervalMs" to if (previousArrival > 0) (arrival - previousArrival) / 1_000_000.0 else null,
            "eventTimestampStepMs" to if (previousEvent > 0) (event.timestamp - previousEvent) / 1_000_000.0 else null,
            "timestampDifferenceMs" to (arrival - event.timestamp) / 1_000_000.0,
            // Legacy field retained for existing offline analysis. It is not proven delivery latency.
            "deliveryDelayMs" to (arrival - event.timestamp) / 1_000_000.0, "valid" to valid,
            "callbacks" to st.callbacks, "validCount" to st.validCount,
            "recentValueAtReceipt" to st.recentValue(arrival), "sourceTimeNote" to st.sourceTimeNote()))
        HeartStream.diagnostics.callback(arrival,valid)
        HeartStream.lastEvent=org.json.JSONObject().put("event","heart").put("sequence",st.callbacks)
            .put("valid",valid).put("bpm",if(valid)bpm else org.json.JSONObject.NULL).put("accuracy",event.accuracy)
            .put("eventNs",event.timestamp).put("arrivalNs",arrival).put("watchReceiptMs",st.lastWallMs)
            .put("screenInteractive",Probe.screen(this)).put("heartAttemptId",HeartStream.diagnosticJson().opt("attemptId"))
            .put("sourceAgeVerified",false).put("sourceTimeNote",st.sourceTimeNote())
        HeartStream.publish(1)
        ble?.publish(1)
    }
    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {
        if (!Probe.state.running || !Probe.state.registered) return
        Probe.state.accuracyChanged(accuracy)
        Probe.log(this, "accuracy", mapOf("accuracy" to accuracy))
        ble?.publish(3)
        HeartStream.publish(3)
    }
    private fun finishCapture(reason: String) {
        CaptureDiagnostics.record(this, "capture_stop")
        ble?.close(); ble = null
        HeartSensorHub.release("continuous")
        handler.removeCallbacksAndMessages(null)
        if (receiverAdded) { unregisterReceiver(screenReceiver); receiverAdded = false }
        Probe.state.stop()
        HeartBroadcast.captureStopped(reason)
        HeartStream.diagnostics.stopped(reason,SystemClock.elapsedRealtimeNanos())
        HeartStream.publish(2)
        Probe.log(this, "session_stop", mapOf("reason" to reason, "callbacks" to Probe.state.callbacks, "validCount" to Probe.state.validCount))
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }
    override fun onTaskRemoved(rootIntent: Intent?) { Probe.log(this, "task_removed"); super.onTaskRemoved(rootIntent) }
    override fun onDestroy() {
        CaptureDiagnostics.record(this, "service_destroy")
        ble?.close(); ble = null
        HeartSensorHub.release("continuous")
        handler.removeCallbacksAndMessages(null)
        if (receiverAdded) { unregisterReceiver(screenReceiver); receiverAdded = false }
        if (Probe.state.running) { Probe.state.stop(); HeartStream.diagnostics.stopped("service_destroy",SystemClock.elapsedRealtimeNanos()); HeartStream.publish(2); Probe.log(this, "session_stop", mapOf("reason" to "service_destroy")) }
        if(hasCaptureSession)HeartBroadcast.captureStopped("service_destroy")
        Probe.log(this, "service_destroy")
        super.onDestroy()
    }
}
