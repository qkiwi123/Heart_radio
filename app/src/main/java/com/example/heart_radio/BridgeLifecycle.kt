package com.example.heart_radio

/** A restart restores an explicitly enabled session on the same boot, without renewing it. */
data class BridgeLease(val startedElapsedMs: Long, val deadlineElapsedMs: Long,
                       val deadlineWallMs: Long, val bootCount: Int, val durationMs: Long = DEFAULT_MS) {
    fun remaining(elapsedMs: Long, wallMs: Long, currentBoot: Int): Long {
        if (bootCount < 0 || currentBoot != bootCount || elapsedMs < startedElapsedMs) return 0
        if (durationMs == 0L) return Long.MAX_VALUE
        return minOf(deadlineElapsedMs - elapsedMs, deadlineWallMs - wallMs).coerceIn(0, MAX_MS)
    }
    companion object {
        const val DEFAULT_MS = 30 * 60_000L
        const val MAX_MS = 365 * 24 * 60 * 60_000L
        fun start(elapsedMs: Long, wallMs: Long, boot: Int, durationMs: Long = DEFAULT_MS): BridgeLease {
            require(durationMs == 0L || durationMs in 1000..MAX_MS)
            return BridgeLease(elapsedMs, if (durationMs == 0L) 0 else elapsedMs + durationMs,
                if (durationMs == 0L) 0 else wallMs + durationMs, boot, durationMs)
        }
    }
}

object StandbyDuration {
    fun parse(unlimited: Boolean, amount: Double, hours: Boolean): Long {
        if (unlimited) return 0
        val ms = amount * (if (hours) 3_600_000 else 60_000)
        require(amount.isFinite() && amount > 0 && ms.isFinite() && ms in 1000.0..BridgeLease.MAX_MS.toDouble()) {
            "时长范围为1秒至365天"
        }
        return kotlin.math.round(ms).toLong()
    }
}

/** A paired computer supplies its remaining time, never a fresh full period on reconnect. */
object StandbySync {
    fun lease(seconds: Double, remainingMs: Long?, elapsedMs: Long, wallMs: Long, boot: Int,
              previous: BridgeLease? = null, sameRevision: Boolean = false): BridgeLease {
        require(seconds.isFinite() && (seconds == 0.0 || seconds in 1.0..BridgeLease.MAX_MS / 1000.0))
        val duration = kotlin.math.round(seconds * 1000).toLong()
        if (duration == 0L) {
            require(remainingMs == null)
            return BridgeLease.start(elapsedMs, wallMs, boot, 0)
        }
        require(remainingMs != null && remainingMs in 0..duration)
        var elapsedDeadline = elapsedMs + remainingMs
        var wallDeadline = wallMs + remainingMs
        if (sameRevision && previous != null && previous.durationMs > 0 && previous.bootCount == boot) {
            elapsedDeadline = minOf(elapsedDeadline, previous.deadlineElapsedMs)
            wallDeadline = minOf(wallDeadline, previous.deadlineWallMs)
        }
        return BridgeLease(elapsedMs, elapsedDeadline, wallDeadline, boot, duration)
    }
}

/** Repeated short connections must back off too; only a stable connection resets failures. */
class BridgeRetryPolicy {
    private var failures = 0
    fun disconnected(connectedMs: Long): Long {
        if (connectedMs >= 30_000) failures = 0
        failures = (failures + 1).coerceAtMost(5)
        return minOf(30_000L, 2_000L shl (failures - 1))
    }
}
