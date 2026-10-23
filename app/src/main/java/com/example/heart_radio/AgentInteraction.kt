package com.example.heart_radio

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import org.json.JSONObject

/** Durable shared business permission. Only explicit local UI edits create an opening event. */
object AgentInteraction {
    private var gate=InteractionSync("watch",SystemClock::elapsedRealtime)
    private lateinit var context: Context
    private var boot=""
    private var storageError=""
    private val handler=Handler(Looper.getMainLooper())
    private var confirmedFingerprint=""
    private var confirmedSelection=""
    private var lastSync=0L
    private val expiry=Runnable { synchronized(this){checkExpiry();schedule()} }
    @Synchronized fun initialize(ctx: Context) {
        context=ctx.applicationContext
        boot=try { Settings.Global.getInt(context.contentResolver,Settings.Global.BOOT_COUNT).also{require(it>=0)}.toString() }
            catch(_:Exception){java.util.UUID.randomUUID().toString()}
        val saved=context.getSharedPreferences("agent-interaction",Context.MODE_PRIVATE).getString("state",null)
        if(saved!=null)try {
            val value=JSONObject(saved);val restored=InteractionSync("watch",SystemClock::elapsedRealtime)
            restored.merge(parse(value.getJSONObject("register")))
            val ends=value.getJSONObject("deadlines")
            listOfNotNull(restored.pc,restored.watch).filter{it.ttlMs!=null}.forEach {
                val end=ends.getLong(it.id);require(end>=0);restored.deadlines[it.id]=end
            }
            if(value.getString("boot")!=boot)restored.edit(false)
            restored.expire();commit(restored)
        }catch(_:Exception){storageError="交互状态无法恢复，请关闭后重试";closeEffects()}
        schedule()
    }
    private fun wire(s:GateSnapshot)=InteractionJson.wire(s)
    private fun parse(s:JSONObject)=InteractionJson.parse(s)
    private fun JSONObject.keySet():Set<String> = keys().asSequence().toSet()
    private fun closeEffects(){
        val close=Runnable { DeviceGateway.cancelAll("interaction_disabled");ReminderEngine.cancelAll("interaction_closed");RfcommProbeService.cancelEffects() }
        if(Looper.myLooper()==Looper.getMainLooper())close.run() else handler.post(close)
    }
    private fun commit(next:InteractionSync) {
        val value=JSONObject().put("boot",boot).put("register",wire(next.snapshot())).put("deadlines",JSONObject(next.deadlines.toMap()))
        if(!context.getSharedPreferences("agent-interaction",Context.MODE_PRIVATE).edit().putString("state",value.toString()).commit()){
            storageError="交互设置保存失败";confirmedFingerprint="";closeEffects();throw IllegalStateException(storageError)
        }
        val changed=next.fingerprint()!=gate.fingerprint()
        val before=gate.enabled
        gate=next;storageError=""
        if(changed)confirmedFingerprint=""
        if(!gate.enabled && (before || changed))closeEffects()
        schedule()
    }
    private fun schedule(){
        handler.removeCallbacks(expiry)
        if(storageError.isNotEmpty()||ProjectSession.suspended)return
        gate.remaining()?.let { handler.postDelayed(expiry,it.coerceAtLeast(1)) }
    }
    private fun checkExpiry(){
        if(gate.enabled && gate.remaining()==0L && storageError.isEmpty()){
            val next=gate.copy();next.expire();try{commit(next)}catch(_:IllegalStateException){ /* fail closed until explicit repair */ }
        }
    }
    @Synchronized fun enabled():Boolean {checkExpiry();return !ProjectSession.suspended && storageError.isEmpty() && gate.enabled}
    @Synchronized fun pauseSession(){handler.removeCallbacks(expiry);closeEffects()}
    @Synchronized fun resumeSession(){checkExpiry();schedule()}
    @Synchronized fun needsRepair()=storageError.isNotEmpty()
    @Synchronized fun set(ctx:Context,value:Boolean) {
        if(value && storageError.isNotEmpty())return
        checkExpiry();val next=gate.copy()
        if(next.edit(value,CapabilityBridgeService.duration(ctx).takeIf{it>0}) || storageError.isNotEmpty())try{commit(next)}catch(_:IllegalStateException){return}
        if(!value)closeEffects()
        if(value)ProjectSession.monitor(ctx)
        Probe.log(ctx,"agent_interaction_changed",mapOf("enabled" to enabled(),"operationVersion" to gate.fingerprint()))
        HeartStream.publish(2)
    }
    @Synchronized fun updateDuration(ctx:Context){
        if(storageError.isNotEmpty())return
        checkExpiry()
        if(gate.enabled){val next=gate.copy();next.edit(true,CapabilityBridgeService.duration(ctx).takeIf{it>0},true);commit(next);HeartStream.publish(2)}
    }
    @Synchronized fun applyRemaining(remainingMs:Long?){
        if(storageError.isNotEmpty())return
        checkExpiry();if(!gate.enabled)return
        if(remainingMs!=null && remainingMs<=0){set(context,false);return}
        // Legacy lease refresh is a clamp only; it cannot silently renew shared permission.
        if(remainingMs!=null){val next=gate.copy();next.deadlines.keys.toList().forEach { id -> next.deadlines[id]=minOf(next.deadlines.getValue(id),SystemClock.elapsedRealtime()+remainingMs) };commit(next)}
    }
    @Synchronized fun sync(params:JSONObject,selection:String?):JSONObject {
        return try {
            check(storageError.isEmpty())
            require(params.keySet()==setOf("selectionId","syncId","state"))
            require(selection!=null && selection==params.getString("selectionId"))
            val id=params.getString("syncId");require(id.matches(Regex("[0-9a-f]{32}")))
            checkExpiry()
            val incoming=parse(params.getJSONObject("state"))
            val received=InteractionSync("watch",SystemClock::elapsedRealtime).also{it.merge(incoming)}.fingerprint()
            val lostHistory=(incoming.watch?.event?.seq?:0)>(gate.watch?.seq?:0)
            val next=gate.copy();next.merge(incoming)
            if(lostHistory)next.edit(false)
            if(next.fingerprint()!=gate.fingerprint() || next.deadlines!=gate.deadlines || storageError.isNotEmpty())commit(next)
            lastSync=SystemClock.elapsedRealtime();confirmedSelection=selection
            confirmedFingerprint=if(received==gate.fingerprint())received else ""
            // Cancellation is synchronous on the main thread before confirming a close.
            if(!enabled())closeEffects()
            JSONObject().put("status","ok").put("syncId",id).put("interaction",state())
        }catch(_:Exception){JSONObject().put("status",if(storageError.isNotEmpty())"interaction_storage_error" else "invalid_interaction_sync")}
    }
    @Synchronized fun state():JSONObject {
        val on=enabled();val conn=ConnectionService.state()
        val confirmed=storageError.isEmpty() && confirmedFingerprint==gate.fingerprint() && confirmedSelection==conn.optString("selectionId") &&
            ConnectionService.connected() && SystemClock.elapsedRealtime()-lastSync<5000
        return JSONObject().put("enabled",on).put("desiredEnabled",gate.enabled).put("syncSupported",true)
            .put("remainingMs",gate.remaining()?:JSONObject.NULL).put("register",wire(gate.snapshot()))
            .put("version",gate.fingerprint()).put("peerConfirmed",confirmed).put("pending",!confirmed)
            .put("storageError",storageError).put("syncStatus",if(storageError.isNotEmpty())storageError else if(confirmed)"两端已同步" else "本端已设置，待同步")
    }
}
