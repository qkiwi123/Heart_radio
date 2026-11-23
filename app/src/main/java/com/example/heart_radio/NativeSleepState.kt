package com.example.heart_radio

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONObject

/** One passive subscription with an Agent-scoped cache and independent diagnostic observers. Main thread only. */
internal object NativeSleepState {
    const val MESSAGE_ID = 490
    private val handler = Handler(Looper.getMainLooper())
    private val observers = linkedSetOf<(Long, Long, JSONObject) -> Unit>()
    private var subscription: AutoCloseable? = null
    private var interactionContext: Context? = null
    private var generation = 0L
    private var subscriptionStartedAtMs = 0L
    private var interactionStartedAtMs = 0L
    private var interactionStartedElapsedMs = 0L
    private var latest: Event? = null
    private var invalidEvent = false
    private val retry = Runnable { startInteractionSubscription() }

    private data class Event(val code: Long, val present: Boolean, val eventAtMs: Long,
                             val receivedAtMs: Long, val receivedElapsedMs: Long)

    fun interactionChanged(context: Context, enabled: Boolean) {
        if (!NativeHealth.supported() || enabled == (interactionContext != null)) return
        val app = context.applicationContext
        clearOffWrist()
        handler.removeCallbacks(retry)
        interactionContext = if (enabled) app else null
        if (enabled) {
            interactionStartedElapsedMs = SystemClock.elapsedRealtime()
            interactionStartedAtMs = System.currentTimeMillis()
            startInteractionSubscription()
        } else {
            closeIfUnused()
            Probe.log(app, "agent_sleep_listener", mapOf("state" to "closed"))
        }
    }

    private fun startInteractionSubscription() {
        val context = interactionContext ?: return
        try {
            open(context)
            Probe.log(context, "agent_sleep_listener", mapOf("state" to "listening"))
        } catch (error: Exception) {
            Probe.log(context, "agent_sleep_listener", mapOf("state" to "registration_failed",
                "error" to error.javaClass.simpleName))
            handler.postDelayed(retry, 30_000)
        }
    }

    fun observe(context: Context, receiver: (Long, Long, JSONObject) -> Unit): AutoCloseable {
        open(context.applicationContext)
        observers.add(receiver)
        return AutoCloseable { observers.remove(receiver); closeIfUnused() }
    }

    private fun open(context: Context) {
        if (subscription != null) return
        val token = ++generation
        subscriptionStartedAtMs = System.currentTimeMillis()
        subscription = NativeHealth.listenSleepPush(context, MESSAGE_ID) { wall, elapsed, packet ->
            handler.post {
                if (generation != token || subscription == null) return@post
                if (interactionContext != null && elapsed >= interactionStartedElapsedMs) {
                    updateLatest(wall, elapsed, packet)
                }
                observers.toList().forEach { it(wall, elapsed, packet) }
            }
        }
    }

    private fun listenerSinceMs() = maxOf(interactionStartedAtMs, subscriptionStartedAtMs)

    private fun updateLatest(wall: Long, elapsed: Long, packet: JSONObject) {
        val fields = packet.optJSONObject("fields")
        val first = fields?.optLong("1") ?: 0L
        val last = fields?.optLong("2") ?: 0L
        val code = packet.optLong("rawCode", -1)
        val present = packet.optBoolean("statePresent")
        val eventAt = first * 1000
        val validRange = 946684800L..minOf(wall / 1000 + 60, Int.MAX_VALUE.toLong())
        val event = if (packet.optString("status") == "received" && code in 0L..1L &&
            (code == 0L || present) && first in validRange && last in validRange &&
            eventAt >= listenerSinceMs() / 1000 * 1000) Event(code, present, eventAt, wall, elapsed) else null
        if (event == null || event.eventAtMs >= (latest?.eventAtMs ?: 0L)) {
            latest = event
            invalidEvent = event == null
        }
    }

    fun clearOffWrist() {
        latest = null
        invalidEvent = false
    }

    private fun closeIfUnused() {
        if (interactionContext != null || observers.isNotEmpty()) return
        generation++
        subscriptionStartedAtMs = 0
        val handle = subscription
        subscription = null
        runCatching { handle?.close() }
    }

    fun snapshot(): JSONObject {
        val event = latest
        val listening = interactionContext != null && subscription != null
        val status = when {
            !listening -> "listener_unavailable"
            event != null -> "event_received"
            invalidEvent -> "invalid_event"
            else -> "awaiting_event"
        }
        val result = JSONObject().put("status", "ok").put("sleepMessageId", MESSAGE_ID)
            .put("sleepEventStatus", status)
        if (listening) result.put("listenerSinceMs", listenerSinceMs())
        if (event != null) result.put("rawCode", event.code).put("fieldPresent", event.present)
            .put("eventAtMs", event.eventAtMs).put("eventReceivedAtMs", event.receivedAtMs)
            .put("eventAgeMs", SystemClock.elapsedRealtime() - event.receivedElapsedMs)
        return result
    }
}
