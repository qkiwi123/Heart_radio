package com.example.heart_radio

import android.os.SystemClock
import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArraySet

/** The single continuous capture publishes to transports; transports never register a sensor. */
object HeartStream {
    val diagnostics = HeartCaptureDiagnostics()
    var startContext = JSONObject()
    var lastEvent: JSONObject? = null
    private val observers = CopyOnWriteArraySet<(Int) -> Unit>()
    fun subscribe(observer: (Int) -> Unit) { observers.add(observer) }
    fun unsubscribe(observer: (Int) -> Unit) { observers.remove(observer) }
    fun publish(kind: Int) { observers.forEach { it(kind) } }
    fun diagnosticJson(): JSONObject {
        val reading=Probe.state
        val view=diagnostics.snapshot(reading,SystemClock.elapsedRealtimeNanos())
        return JSONObject().put("schemaVersion",1).put("state",view.state).put("message",view.message)
            .put("attemptId",view.attemptId?:JSONObject.NULL).put("elapsedMs",view.elapsedMs?:JSONObject.NULL)
            .put("firstCallbackAfterMs",view.firstCallbackAfterMs?:JSONObject.NULL).put("firstValidAfterMs",view.firstValidAfterMs?:JSONObject.NULL)
            .put("lastCallbackReceiptAgeMs",view.lastCallbackAgeMs?:JSONObject.NULL).put("lastValidReceiptAgeMs",view.lastValidReceiptAgeMs?:JSONObject.NULL)
            .put("firstCallbackTimeoutMs",HeartCaptureDiagnostics.FIRST_CALLBACK_TIMEOUT_MS).put("firstCallbackTimedOut",view.firstCallbackTimedOut)
            .put("timeoutAction","report_only").put("running",reading.running).put("registered",reading.registered)
            .put("callbacks",reading.callbacks).put("validCount",reading.validCount).put("stopReason",view.stopReason?:JSONObject.NULL)
            .put("failureReason",view.failureReason?:JSONObject.NULL).put("startContext",JSONObject(startContext.toString()))
            .put("sourceAgeVerified",false).put("sourceTimeNote",reading.sourceTimeNote())
    }
}
