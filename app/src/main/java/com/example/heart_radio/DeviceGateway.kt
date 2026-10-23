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
        if(request.optString("op") in setOf("remind","reminder_status")){
            reply(ReminderEngine.execute(request).put("id",request.optString("id")));return
        }
        engine(context).execute(request, reply)
    }
    fun cancelAll(reason:String="disconnected") { engine?.cancelAll(reason) }
}
