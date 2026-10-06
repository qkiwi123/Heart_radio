package com.example.heart_radio

/** Receipt recency is not sample freshness. Never replace the raw sensor timestamp. */
class ReadingState {
    var running = false
    var registered = false
    var result = "尚未注册"
    var startedNs = 0L
    var callbacks = 0
    var validCount = 0
    var lastEventNs = 0L
    var lastDeliveryNs = 0L
    var lastValidNs = 0L
    var lastValidDeliveryNs = 0L
    var lastTimestampOrdered = false
    var lastWallMs = 0L
    var value = Float.NaN
    var accuracy = Int.MIN_VALUE
    var lastValid = false
    fun reset(now: Long) {
        running = true; registered = false; result = "正在注册"; startedNs = now
        callbacks = 0; validCount = 0; lastEventNs = 0; lastDeliveryNs = 0
        lastValidNs = 0; lastValidDeliveryNs = 0; lastTimestampOrdered = false
        lastWallMs = 0; value = Float.NaN; accuracy = Int.MIN_VALUE; lastValid = false
    }
    fun accept(bpm: Float, status: Int, eventNs: Long, arrivalNs: Long, wallMs: Long): Boolean {
        if (!running || !registered) return false
        callbacks++; value = bpm; accuracy = status; lastWallMs = wallMs; lastDeliveryNs = arrivalNs
        val ordered = eventNs > 0 && eventNs <= arrivalNs && (lastEventNs == 0L || eventNs > lastEventNs)
        lastTimestampOrdered = ordered
        lastValid = bpm.isFinite() && bpm > 0f && status in 1..3 && ordered
        lastEventNs = eventNs
        if (lastValid) { validCount++; lastValidNs = eventNs; lastValidDeliveryNs = arrivalNs }
        return lastValid
    }
    fun status(now: Long, permission: Boolean, found: Boolean): String = when {
        !permission -> "无身体传感器权限"
        !found -> "未找到标准心率传感器"
        !running -> if (result.startsWith("失败")) result else "已停止 / 尚未开始"
        !registered -> result
        callbacks == 0 -> if (now - startedNs > STALE_NS) "30 秒仍无回调" else "等待心率回调"
        now < lastDeliveryNs -> "接收时间异常"
        now - lastDeliveryNs > STALE_NS -> "30 秒无新回调"
        accuracy == -1 -> "无接触，请佩戴"
        !lastValid || accuracy !in 1..3 -> "数据无效（数值、精度或时间戳）"
        else -> "已收到有效数值"
    }
    /** A recently received value, explicitly NOT a claim about actual sample age. */
    fun recentValue(now: Long): Float? = if (running && registered && lastValid && accuracy in 1..3 &&
        lastDeliveryNs > 0 && now >= lastDeliveryNs && now - lastDeliveryNs <= STALE_NS) value else null
    fun sourceTimeNote(): String = when {
        callbacks == 0 -> "采样时间未经验证"
        !lastTimestampOrdered -> "源时间戳异常，未校正"
        lastDeliveryNs - lastEventNs > STALE_NS -> "源时间戳落后，未校正"
        else -> "采样时间未经验证"
    }
    fun accuracyChanged(status: Int) {
        accuracy = status
        // A later accuracy-only notification must not resurrect a value invalidated by lost contact.
        if (status !in 1..3) lastValid = false
    }
    fun stop() { running = false; registered = false }
    companion object { const val STALE_NS = 30_000_000_000L }
}
