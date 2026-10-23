package com.example.heart_radio

import android.content.Context
import android.content.Intent
import android.os.SystemClock
import android.provider.Settings
import org.json.JSONObject

/** One human switch for continuous capture; independent of Agent permission and temporary leases. */
object HeartBroadcast {
    private lateinit var context:Context
    private var gate=InteractionSync("watch",SystemClock::elapsedRealtime)
    private val applyGuard=BroadcastApplyGuard()
    private var storageError=""
    private var effectError=""
    private var boot=""
    private var confirmedVersion=""
    private var confirmedSelection=""
    private var lastSync=0L
    @Synchronized fun initialize(ctx:Context){
        context=ctx.applicationContext
        boot=try{Settings.Global.getInt(context.contentResolver,Settings.Global.BOOT_COUNT).also{require(it>=0)}.toString()}catch(_:Exception){java.util.UUID.randomUUID().toString()}
        val saved=context.getSharedPreferences("heart-broadcast",Context.MODE_PRIVATE).getString("state",null)
        if(saved!=null)try{
            val value=JSONObject(saved);val restored=InteractionSync("watch",SystemClock::elapsedRealtime)
            restored.merge(InteractionJson.parse(value.getJSONObject("register")))
            // Capture is START_NOT_STICKY. Restoring preferences cannot silently restart it.
            if(!ProjectSession.suspended&&(restored.enabled||value.getString("boot")!=boot))restored.edit(false)
            commit(restored)
        }catch(_:Exception){storageError="心率广播状态无法恢复，请关闭后重试"}
    }
    private fun commit(next:InteractionSync){
        val value=JSONObject().put("boot",boot).put("register",InteractionJson.wire(next.snapshot()))
        if(!context.getSharedPreferences("heart-broadcast",Context.MODE_PRIVATE).edit().putString("state",value.toString()).commit()){
            storageError="心率广播保存失败，已停止采集";confirmedVersion=""
            context.stopService(Intent(context,CaptureService::class.java));throw IllegalStateException(storageError)
        }
        if(next.fingerprint()!=gate.fingerprint())confirmedVersion=""
        gate=next;storageError=""
    }
    private fun apply(){
        if(storageError.isNotEmpty()||ProjectSession.suspended)return
        val action=applyGuard.next(gate.fingerprint(),gate.enabled,Probe.state.running)?:return
        try{
            if(action)context.startForegroundService(Intent(context,CaptureService::class.java)
                .setAction("HEART_SYNC_START").putExtra("broadcastVersion",gate.fingerprint()))
            else context.stopService(Intent(context,CaptureService::class.java))
        }catch(e:Exception){captureStopped("开启失败：${e.javaClass.simpleName}")}
    }
    @Synchronized fun desired()=storageError.isEmpty()&&gate.enabled
    @Synchronized fun needsRepair()=storageError.isNotEmpty()
    @Synchronized fun acceptsStart(version:String)=desired()&&version==gate.fingerprint()
    @Synchronized fun resumeSession(){applyGuard.invalidate();apply()}
    @Synchronized fun set(value:Boolean){
        if(value&&storageError.isNotEmpty())return
        val next=gate.copy();next.edit(value,force=value)
        try{commit(next);effectError="";apply();HeartStream.publish(2)}catch(_:Exception){ /* state exposes failure */ }
    }
    @Synchronized fun externalStart():Boolean {
        if(storageError.isNotEmpty()||ProjectSession.suspended)return false
        if(!gate.enabled){val next=gate.copy();next.edit(true);try{commit(next)}catch(_:Exception){return false}}
        effectError="";return true
    }
    @Synchronized fun captureStopped(reason:String){
        if(ProjectSession.suspended)return
        if(storageError.isNotEmpty())return
        if(gate.enabled){val next=gate.copy();next.edit(false);try{commit(next)}catch(_:Exception){return}}
        if(reason=="permission_lost")effectError="身体传感器权限已失效，采集已停止"
        else if(reason=="registration_false")effectError="心率监听注册失败，采集已停止"
        else if(reason=="failure")effectError="采集已停止：${Probe.state.result}"
        else if(reason.startsWith("开启失败"))effectError=reason
    }
    @Synchronized fun sync(params:JSONObject,selection:String?):JSONObject {
        return try{
            check(storageError.isEmpty())
            require(params.keys().asSequence().toSet()==setOf("selectionId","syncId","state"))
            require(selection!=null&&selection==params.getString("selectionId"))
            val id=params.getString("syncId");require(id.matches(Regex("[0-9a-f]{32}")))
            val incoming=InteractionJson.parse(params.getJSONObject("state"))
            require(listOfNotNull(incoming.pc,incoming.watch).all{it.event.ttlMs==null})
            val received=InteractionSync("watch",SystemClock::elapsedRealtime).also{it.merge(incoming)}.fingerprint()
            val next=gate.copy();next.merge(incoming)
            if((incoming.watch?.event?.seq?:0)>(gate.watch?.seq?:0))next.edit(false)
            if(next.fingerprint()!=gate.fingerprint()){commit(next);effectError=""}
            apply()
            confirmedSelection=selection;lastSync=SystemClock.elapsedRealtime()
            confirmedVersion=if(received==gate.fingerprint())received else ""
            JSONObject().put("status","ok").put("syncId",id).put("broadcast",state())
        }catch(_:Exception){JSONObject().put("status",if(storageError.isNotEmpty())"broadcast_storage_error" else "invalid_broadcast_sync")}
    }
    @Synchronized fun state():JSONObject {
        val running=Probe.state.running;val registered=Probe.state.registered
        val applied=if(gate.enabled)running&&registered else !running
        val confirmed=storageError.isEmpty()&&confirmedVersion==gate.fingerprint()&&confirmedSelection==ConnectionService.selectionId()&&ConnectionService.connected()&&SystemClock.elapsedRealtime()-lastSync<5000&&applied
        return JSONObject().put("enabled",desired()).put("desiredEnabled",gate.enabled).put("running",running).put("registered",registered)
            .put("register",InteractionJson.wire(gate.snapshot())).put("version",gate.fingerprint()).put("syncSupported",true)
            .put("peerConfirmed",confirmed).put("pending",!confirmed).put("storageError",storageError).put("effectError",effectError)
            .put("suspended",ProjectSession.suspended)
            .put("syncStatus",if(storageError.isNotEmpty())storageError else if(ProjectSession.suspended)"项目已暂停，保留原设置；开启连接后恢复" else if(effectError.isNotEmpty())effectError else if(confirmed)"两端已同步" else "本端已设置，待同步")
    }
}
