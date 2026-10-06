package com.example.heart_radio

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Process
import android.os.SystemClock

/** Read-only, coarse diagnostics. Never schedules a wakeup or records SSIDs/other apps. */
object CaptureDiagnostics {
    fun record(context: Context, reason: String) {
        try {
            val battery = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
            val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
            val manager = context.getSystemService(ConnectivityManager::class.java)
            val wifi = manager.allNetworks.any { manager.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true }
            val counter = context.getSystemService(BatteryManager::class.java).getIntProperty(BatteryManager.BATTERY_PROPERTY_CHARGE_COUNTER)
            Probe.log(context, "capture_diagnostic", mapOf(
                "reason" to reason, "pid" to Process.myPid(), "uptimeMs" to SystemClock.uptimeMillis(),
                "batteryPct" to if (level >= 0 && scale > 0) level * 100.0 / scale else null,
                "batteryStatus" to battery?.getIntExtra(BatteryManager.EXTRA_STATUS, -1),
                "plugged" to battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED, -1),
                "temperatureTenthsC" to battery?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1),
                "chargeCounterUah" to counter.takeIf { it != Int.MIN_VALUE },
                "wifiNetworkPresent" to wifi, "wifiStatus" to Probe.wifiStatus,
                "callbacks" to Probe.state.callbacks, "validCount" to Probe.state.validCount,
                "logBytes" to Probe.logFile?.length()))
        } catch (e: Exception) {
            Probe.log(context, "diagnostic_error", mapOf("reason" to e.javaClass.simpleName))
        }
    }
}
