package com.example.heart_radio

import android.content.Context
import android.content.SharedPreferences
import android.os.SystemClock
import org.json.JSONObject

/** Settings edits only come from local human controls; transport only merges existing events. */
object ReminderPolicy{
    private lateinit var preferences:SharedPreferences
    private var register=ReminderPolicyRegister("watch")
    private var error="";private var confirmed="";private var selection="";private var syncAt=0L
    @Synchronized fun initialize(ctx:Context){
        preferences=ctx.applicationContext.getSharedPreferences("reminder-policy",Context.MODE_PRIVATE)
        val saved=preferences.getString("state",null)
        if(saved!=null)try{register.merge(parse(JSONObject(saved)))}catch(_:Exception){error="提醒设置无法恢复，已暂停新提醒"}
    }
    @Synchronized fun limits()=register.effective()
    fun limitsJson(v:ReminderLimits=limits())=JSONObject().put("vibrationEnabled",v.vibrationEnabled).put("maxVibrationMs",v.maxVibrationMs)
        .put("wakeEnabled",v.wakeEnabled).put("maxScreenSeconds",v.maxScreenSeconds).put("soundEnabled",v.soundEnabled)
    private fun keys(v:JSONObject)=v.keys().asSequence().toSet()
    private fun number(v:JSONObject,key:String):Long{val n=v.get(key);require(n is Int||n is Long);return(n as Number).toLong()}
    private fun settings(v:JSONObject):ReminderLimits{
        val legacy=setOf("vibrationEnabled","maxVibrationMs","wakeEnabled","maxScreenSeconds")
        require(keys(v)==legacy || keys(v)==legacy+"soundEnabled")
        require(v.get("vibrationEnabled") is Boolean&&v.get("wakeEnabled") is Boolean)
        require(!v.has("soundEnabled") || v.get("soundEnabled") is Boolean)
        val screen=number(v,"maxScreenSeconds");require(screen in 5..30)
        return ReminderLimits(v.getBoolean("vibrationEnabled"),number(v,"maxVibrationMs"),v.getBoolean("wakeEnabled"),screen.toInt(),v.optBoolean("soundEnabled",false)).also{it.validate()}
    }
    private fun wire(s:PolicySnapshot):JSONObject{
        fun event(e:PolicyEvent?):Any=e?.let{JSONObject().put("seq",it.seq).put("seen",it.seen).put("id",it.id).put("value",limitsJson(it.value))}?:JSONObject.NULL
        return JSONObject().put("pc",event(s.pc)).put("watch",event(s.watch))
    }
    private fun parse(v:JSONObject):PolicySnapshot{
        require(keys(v)==setOf("pc","watch"))
        fun event(key:String):PolicyEvent?{
            if(v.isNull(key))return null
            val e=v.getJSONObject(key);require(keys(e)==setOf("seq","seen","id","value"));require(e.get("id") is String)
            return PolicyEvent(number(e,"seq"),number(e,"seen"),e.getString("id"),settings(e.getJSONObject("value")))
        }
        return PolicySnapshot(event("pc"),event("watch"))
    }
    private fun commit(next:ReminderPolicyRegister){
        if(next.fingerprint()==register.fingerprint())return
        if(!preferences.edit().putString("state",wire(next.snapshot()).toString()).commit()){
            error="提醒设置保存失败，已暂停新提醒";ReminderEngine.cancelAll("reminder_policy_storage_error");throw IllegalStateException(error)
        }
        val changed=next.effective()!=register.effective();register=next;confirmed=""
        if(changed)ReminderEngine.cancelAll("reminder_policy_changed")
    }
    @Synchronized fun edit(v:ReminderLimits){check(error.isEmpty()){error};val next=register.copy();next.edit(v);commit(next)}
    @Synchronized fun state():JSONObject{
        val connected=ConnectionService.connected()&&selection==ConnectionService.state().optString("selectionId")
        val ack=error.isEmpty()&&connected&&confirmed==register.fingerprint()&&SystemClock.elapsedRealtime()-syncAt<5000
        return JSONObject().put("state",wire(register.snapshot())).put("limits",limitsJson()).put("version",register.fingerprint())
            .put("peerConfirmed",ack).put("storageError",error).put("syncStatus",if(error.isNotEmpty())error else if(ack)"两端已同步" else "本端已设置，待同步")
    }
    @Synchronized fun sync(params:JSONObject,currentSelection:String?):JSONObject=try{
        check(error.isEmpty()){error}
        require(keys(params)==setOf("selectionId","syncId","state"));require(currentSelection!=null&&params.getString("selectionId")==currentSelection)
        val id=params.getString("syncId");require(id.matches(Regex("[0-9a-f]{32}")))
        val incoming=parse(params.getJSONObject("state"));val received=ReminderPolicyRegister("watch").also{it.merge(incoming)}.fingerprint()
        val next=register.copy();next.merge(incoming)
        if((incoming.watch?.seq?:0)>(register.watch?.seq?:0)){
            val before=register.effective();val merged=next.effective()
            next.edit(ReminderLimits(before.vibrationEnabled&&merged.vibrationEnabled,minOf(before.maxVibrationMs,merged.maxVibrationMs),
                before.wakeEnabled&&merged.wakeEnabled,minOf(before.maxScreenSeconds,merged.maxScreenSeconds),before.soundEnabled&&merged.soundEnabled))
        }
        commit(next)
        confirmed=if(received==register.fingerprint())received else "";selection=currentSelection;syncAt=SystemClock.elapsedRealtime()
        JSONObject().put("status","ok").put("syncId",id).put("policy",state())
    }catch(_:Exception){JSONObject().put("status",if(error.isEmpty())"invalid_reminder_policy_sync" else "reminder_policy_storage_error")}
    @Synchronized fun rejection(spec:ReminderSpec):JSONObject?{
        if(error.isNotEmpty())return JSONObject().put("status","reminder_policy_storage_error")
        val reason=limits().violation(spec.vibrate,spec.vibration.totalMs(),spec.screen,spec.screenSeconds,spec.sound)?:return null
        return JSONObject().put("status","reminder_policy_denied").put("reason",reason).put("limits",limitsJson())
    }
}
