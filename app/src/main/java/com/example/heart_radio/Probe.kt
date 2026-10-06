package com.example.heart_radio

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorManager
import android.os.Build
import android.os.PowerManager
import android.os.SystemClock
import org.json.JSONObject
import java.io.File
import java.time.Instant

object Probe {
    val state = ReadingState()
    var logFile: File? = null
    var logError = ""
    var bleStatus = "蓝牙未开启"
    var wifiStatus = "Wi-Fi 未开启"
    fun bluetoothAllowed(c: Context) = Build.VERSION.SDK_INT < 31 ||
        listOf("android.permission.BLUETOOTH_CONNECT", "android.permission.BLUETOOTH_ADVERTISE").all {
            c.checkSelfPermission(it) == android.content.pm.PackageManager.PERMISSION_GRANTED
        }
    fun permission() = if (Build.VERSION.SDK_INT >= 36) "android.permission.health.READ_HEART_RATE" else "android.permission.BODY_SENSORS"
    fun allowed(c: Context) = c.checkSelfPermission(permission()) == android.content.pm.PackageManager.PERMISSION_GRANTED
    fun screen(c: Context) = (c.getSystemService(Context.POWER_SERVICE) as PowerManager).isInteractive
    fun manager(c: Context) = c.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    fun sensor(c: Context) = manager(c).getDefaultSensor(Sensor.TYPE_HEART_RATE)
    fun startLog(c: Context) {
        logError = ""
        val dir = File(c.filesDir, "logs").apply { mkdirs() }
        logFile = File(dir, "session-${System.currentTimeMillis()}-${SystemClock.elapsedRealtime()}.jsonl")
    }
    fun log(c: Context, kind: String, fields: Map<String, Any?> = emptyMap()) {
        val now = System.currentTimeMillis()
        val row = JSONObject().put("kind", kind).put("wallMs", now).put("iso", Instant.ofEpochMilli(now).toString())
            .put("elapsedNs", SystemClock.elapsedRealtimeNanos()).put("screenInteractive", screen(c))
            .put("running", state.running).put("registered", state.registered)
        fields.forEach { (k, v) -> row.put(k, v ?: JSONObject.NULL) }
        try {
            val dir = File(c.filesDir, "logs").apply { mkdirs() }
            (logFile ?: File(dir, "lifecycle.jsonl")).appendText(row.toString() + "\n", Charsets.UTF_8)
        } catch (e: Exception) { logError = "日志写入失败：${e.javaClass.simpleName}: ${e.message}" }
    }
    fun inventory(c: Context): String {
        val all = manager(c).getSensorList(Sensor.TYPE_ALL)
        log(c, "inventory", mapOf("permission" to allowed(c), "count" to all.size,
            "sensors" to org.json.JSONArray(all.map { s -> JSONObject().put("name", s.name).put("type", s.type).put("stringType", s.stringType) })))
        return "App 可见传感器（${all.size}）\n" + all.joinToString("\n") { "${it.name} · 类型 ${it.type}\n${it.stringType}" }
    }
}
