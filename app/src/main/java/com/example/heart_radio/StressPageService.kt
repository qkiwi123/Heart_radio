package com.example.heart_radio

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONObject

/** Reads the stock pressure page only for the lifetime of an explicit request. Main thread only. */
class StressPageService : AccessibilityService() {
    companion object {
        private const val STOCK = "com.heytap.wearable.relax"
        private const val ACTION = "heytap.wearable.intent.action.health.pressure.START_PRESSURE"
        private const val PAGE_TIMEOUT_MS = 65_000L
        var connected: StressPageService? = null
            private set
    }

    private class Reading(val id: String, val reply: (JSONObject) -> Unit) {
        var measuringAt = 0L
        var measuringUptime = 0L
        var launched = false
        var sawStock = false
        var pressedEnter = false
        var pressedRecentMeasure = false
        var display: PowerManager.WakeLock? = null
    }

    private val measuringLabels = setOf("正在测量", "正在測量", "Measuring")
    private val failureLabels = setOf("未测得有效值", "未測得有效值", "Poor recording")
    private val enterLabels = setOf("进入测量", "進入測量", "Measure now")
    private val measureLabels = setOf("重测", "重測", "Measure")
    private val handler = Handler(Looper.getMainLooper())
    private var current: Reading? = null
    private val timeout = Runnable { finish("no_fresh_result") }
    private val poll = object : Runnable {
        override fun run() {
            inspect()
            if (current?.launched == true) handler.postDelayed(this, 350)
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        connected = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        val reading = current ?: return
        if (event == null || event.packageName?.toString() != STOCK || !reading.launched) return
        // A dialog can announce its title even when Android supplies no active window root.
        if (reading.measuringAt != 0L && event.eventTime >= reading.measuringUptime &&
            event.text.any { it.toString().trim() in failureLabels } && AgentInteraction.enabled()) {
            finish("stock_no_valid_reading")
            return
        }
        inspect(event)
    }

    override fun onInterrupt() = finish("accessibility_interrupted")
    override fun onDestroy() {
        if (connected === this) connected = null
        finish("accessibility_disconnected")
        super.onDestroy()
    }

    fun cancel(id: String?, reason: String): Boolean {
        val reading = current ?: return false
        if (id != null && id != reading.id) return false
        finish(reason)
        return true
    }

    fun start(request: JSONObject, reply: (JSONObject) -> Unit) {
        val id = request.getString("id")
        if (current != null) {
            reply(JSONObject().put("id", id).put("op", "health_stress").put("status", "busy"))
            return
        }
        val reading = Reading(id, reply)
        current = reading
        val wear = JSONObject().put("id", id).put("op", "health_now")
            .put("params", JSONObject().put("metric", "wear"))
        NativeHealth.execute(this, wear) { result ->
            if (current !== reading) return@execute
            val code = result.optInt("rawCode", -1)
            when {
                !AgentInteraction.enabled() -> finish("interaction_disabled")
                result.optString("status") == "busy" -> finish("busy")
                result.optString("status") != "ok" -> finish("wear_unconfirmed")
                code == 0 -> finish("off_wrist")
                code != 1 || !result.optBoolean("fieldPresent") -> finish("wear_unconfirmed")
                else -> launch(reading)
            }
        }
    }

    @Suppress("DEPRECATION")
    private fun launch(reading: Reading) {
        try {
            // Do not attach a new request to a measurement already visible on the watch.
            val root = rootInActiveWindow
            if (root != null) {
                try {
                    if (root.packageName?.toString() == STOCK && text(root, "measure_title_tv") in measuringLabels) {
                        finish("stock_measurement_active")
                        return
                    }
                } finally { root.recycle() }
            }
            val power = getSystemService(PowerManager::class.java)
            reading.display = power.newWakeLock(PowerManager.SCREEN_BRIGHT_WAKE_LOCK or
                PowerManager.ACQUIRE_CAUSES_WAKEUP, "HeartRadio:stress-page")
            reading.display!!.acquire(PAGE_TIMEOUT_MS + 1000)
            startActivity(Intent(ACTION).setPackage(STOCK).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            reading.launched = true
            handler.postDelayed(timeout, PAGE_TIMEOUT_MS)
            handler.post(poll)
        } catch (_: RuntimeException) { finish("stock_page_unavailable") }
    }

    @Suppress("DEPRECATION")
    private fun text(root: AccessibilityNodeInfo, id: String): String? {
        val nodes = root.findAccessibilityNodeInfosByViewId("$STOCK:id/$id")
        return try {
            nodes.firstOrNull { it.isVisibleToUser }?.text?.toString()?.trim()
        } finally { nodes.forEach { it.recycle() } }
    }

    @Suppress("DEPRECATION")
    private fun press(root: AccessibilityNodeInfo, id: String, labels: Set<String>): Boolean {
        val nodes = root.findAccessibilityNodeInfosByViewId("$STOCK:id/$id")
        try {
            val button = nodes.firstOrNull { it.isVisibleToUser && it.isEnabled && it.isClickable &&
                it.text?.toString()?.trim() in labels } ?: return false
            button.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            // One attempt even if Android cannot confirm delivery.
            return true
        } finally { nodes.forEach { it.recycle() } }
    }

    @Suppress("DEPRECATION")
    private fun showsFailureDialog(root: AccessibilityNodeInfo): Boolean = failureLabels.any { title ->
        val matches = root.findAccessibilityNodeInfosByText(title)
        try {
            matches.any { it.isVisibleToUser && it.packageName?.toString() == STOCK &&
                (it.text?.toString()?.trim() == title || it.contentDescription?.toString()?.trim() == title) }
        } finally { matches.forEach { it.recycle() } }
    }

    @Suppress("DEPRECATION")
    private fun inspect(event: AccessibilityEvent? = null) {
        val reading = current ?: return
        if (!AgentInteraction.enabled()) { finish("interaction_disabled"); return }
        try {
            val root = rootInActiveWindow ?: event?.takeIf {
                reading.measuringAt != 0L && it.eventTime >= reading.measuringUptime
            }?.source ?: return
            try {
                if (root.packageName?.toString() != STOCK) {
                    if (reading.measuringAt != 0L) finish("stock_page_left")
                    return
                }
                reading.sawStock = true
                val title = text(root, "measure_title_tv")
                val score = text(root, "pressure_tv")?.toIntOrNull()
                val sawMeasurement = reading.measuringAt != 0L
                if (sawMeasurement && (title in failureLabels || (title !in measuringLabels && showsFailureDialog(root)))) {
                    finish("stock_no_valid_reading")
                } else if (reading.measuringAt != 0L && score != null && score in 1..100) {
                    finish("ok", score)
                } else if (score !in 1..100 && title in measuringLabels) {
                    if (!sawMeasurement) {
                        reading.measuringAt = System.currentTimeMillis()
                        reading.measuringUptime = SystemClock.uptimeMillis()
                    }
                } else if (reading.measuringAt == 0L) {
                    if (!reading.pressedEnter) reading.pressedEnter = press(root, "enter_btn", enterLabels)
                    // The stock "recent pressure" panel also has the manual start button (重测).
                    // Only start from that panel; never retry after seeing measuring or failure.
                    if (!reading.pressedRecentMeasure && text(root, "last_pressure_tip_tv") != null) {
                        reading.pressedRecentMeasure = press(root, "retry_measure_btn", measureLabels)
                    }
                }
            } finally { root.recycle() }
        } catch (_: RuntimeException) { finish("stock_page_unreadable") }
    }

    private fun finish(status: String, score: Int? = null) {
        val reading = current ?: return
        current = null
        handler.removeCallbacks(timeout)
        handler.removeCallbacks(poll)
        reading.display?.let { if (it.isHeld) it.release() }
        val result = JSONObject().put("id", reading.id).put("op", "health_stress").put("status", status)
        if (reading.launched) result.put("pageStage", when {
            reading.measuringAt != 0L -> "measuring_seen"
            reading.sawStock -> "stock_page_seen"
            else -> "stock_page_unobserved"
        }).put("enterAttempted", reading.pressedEnter).put("recentMeasureAttempted", reading.pressedRecentMeasure)
        if (score != null) result.put("score", score).put("source", "stock_pressure_page")
            .put("measuringSeenAtMs", reading.measuringAt).put("observedAtMs", System.currentTimeMillis())
        reading.reply(result)
    }
}
