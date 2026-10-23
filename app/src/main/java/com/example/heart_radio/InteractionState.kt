package com.example.heart_radio

/** Business permission is independent of either connection or continuous capture. */
class InteractionState {
    var enabled = false; private set
    var deadlineMs: Long? = null; private set
    fun set(value: Boolean, nowMs: Long, durationMs: Long) {
        require(durationMs >= 0)
        if (value == enabled) return
        enabled = value
        deadlineMs = if (value && durationMs > 0) nowMs + durationMs else null
    }
    fun expire(nowMs: Long): Boolean {
        if (enabled && deadlineMs?.let { nowMs >= it } == true) {
            enabled = false; deadlineMs = null; return true
        }
        return false
    }
    fun updateDuration(nowMs: Long, durationMs: Long) {
        require(durationMs >= 0)
        if (enabled) deadlineMs = if (durationMs > 0) nowMs + durationMs else null
    }
}
