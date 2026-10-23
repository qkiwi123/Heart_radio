package com.example.heart_radio

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONObject

/** A stopped session keeps business preferences, but cannot acquire any project resources. */
object ProjectSession {
    @Volatile var suspended=false;private set
    private var stoppedAt=0L
    private val handler=Handler(Looper.getMainLooper())
    private val offline=OfflineTimeout()
    private var monitoring=false
    private lateinit var app:android.app.Application
    private val tick=object:Runnable{override fun run(){
        if(suspended){monitoring=false;return}
        val busy=ConnectionService.running()||Probe.state.running||RfcommProbeService.running()||CapabilityBridgeService.isRunning()||AgentInteraction.enabled()
        if(!busy){monitoring=false;return}
        if(offline.expired(SystemClock.elapsedRealtime(),ConnectionService.connected())){suspend(app);return}
        handler.postDelayed(this,1000)
    }}
    fun monitor(context:Context){
        if(suspended||monitoring)return
        app=context.applicationContext as android.app.Application;monitoring=true;offline.start(SystemClock.elapsedRealtime());handler.postDelayed(tick,1000)
    }
    private fun prefs(context:Context)=context.getSharedPreferences("project-session",Context.MODE_PRIVATE)
    fun initialize(context:Context){
        suspended=prefs(context).getBoolean("suspended",false)
        stoppedAt=prefs(context).getLong("stoppedAtMs",0)
    }
    fun state()=JSONObject().put("suspended",suspended).put("reason",if(suspended)"offline_timeout" else JSONObject.NULL)
        .put("timeoutMs",600_000).put("stoppedAtMs",stoppedAt)
    fun resume(context:Context):Boolean {
        if(!suspended)return true
        if(!prefs(context).edit().putBoolean("suspended",false).commit())return false
        suspended=false
        AgentInteraction.resumeSession()
        HeartBroadcast.resumeSession()
        Probe.log(context,"project_session_resumed",mapOf("heartDesired" to HeartBroadcast.desired(),"agentEnabled" to AgentInteraction.enabled()))
        return true
    }
    fun suspend(context:Context){
        if(suspended)return
        // Set the latch before stopping capture: its destroy callback must retain the user's setting.
        suspended=true;stoppedAt=System.currentTimeMillis()
        monitoring=false;handler.removeCallbacks(tick)
        val saved=prefs(context).edit().putBoolean("suspended",true).putLong("stoppedAtMs",stoppedAt).commit()
        AgentInteraction.pauseSession()
        DeviceGateway.cancelAll("offline_timeout")
        ReminderEngine.cancelAll("offline_timeout")
        RfcommProbeService.cancelEffects()
        context.stopService(Intent(context,CaptureService::class.java))
        context.stopService(Intent(context,CapabilityBridgeService::class.java))
        context.stopService(Intent(context,RfcommProbeService::class.java))
        ConnectionService.timeoutStop(context)
        Probe.log(context,"project_session_suspended",mapOf("reason" to "offline_timeout","stateSaved" to saved,"heartDesired" to HeartBroadcast.desired()))
        context.getSystemService(ActivityManager::class.java).appTasks.forEach{runCatching{it.finishAndRemoveTask()}}
    }
}
