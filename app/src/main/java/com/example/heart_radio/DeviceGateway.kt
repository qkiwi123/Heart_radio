package com.example.heart_radio

import android.content.Context
import android.os.Handler
import android.os.Looper
import org.json.JSONObject

/** One bounded remote engine and one permission check shared by both adapters. Main thread only. */
object DeviceGateway {
    private var engine: CapabilityEngine? = null
    fun engine(context: Context): CapabilityEngine = engine ?: CapabilityEngine(context.applicationContext, Handler(Looper.getMainLooper())).also { engine = it }
    fun execute(context: Context, request: JSONObject, reply: (JSONObject) -> Unit) {
        if (request.optString("op")!="cancel"&&!AgentInteraction.enabled()) {
            reply(JSONObject().put("id", request.optString("id")).put("status", "interaction_disabled")); return
        }
        if (request.optString("op") == "health_stress") {
            val service = StressPageService.connected
            val status = when {
                request.optJSONObject("params")?.length() != 0 -> "invalid_request"
                !NativeHealth.supported() -> "unverified_firmware"
                service == null -> "accessibility_disabled"
                else -> null
            }
            if (status != null) reply(JSONObject().put("id", request.optString("id")).put("op", "health_stress").put("status", status))
            else service!!.start(request, reply)
            return
        }
        val target = request.optJSONObject("params")?.optString("targetId")
        if (request.optString("op") == "cancel" && !target.isNullOrEmpty() &&
            StressPageService.connected?.cancel(target, "cancelled") == true) {
            reply(JSONObject().put("id", request.optString("id")).put("status", "ok"))
            return
        }
        if(request.optString("op") in setOf("remind","reminder_status")){
            reply(ReminderEngine.execute(request).put("id",request.optString("id")));return
        }
        if (request.optString("op") in setOf("health_now", "health_history")) {
            NativeHealth.execute(context, request, reply)
            return
        }
        engine(context).execute(request, reply)
    }
    fun cancelAll(reason:String="disconnected") {
        StressPageService.connected?.cancel(null, reason)
        NativeHealth.cancel()
        engine?.cancelAll(reason)
    }
}
