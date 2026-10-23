package com.example.heart_radio

import android.app.*
import android.content.Context
import android.content.Intent
import android.os.*
import android.util.AtomicFile
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.CopyOnWriteArraySet

/** Main-thread reminder owner, independent of either wireless adapter. */
object ReminderEngine {
    private lateinit var context:Context
    private lateinit var file:AtomicFile
    private val journal=ReminderJournal<JSONObject>()
    private val handler=Handler(Looper.getMainLooper())
    private val observers=CopyOnWriteArraySet<(JSONObject)->Unit>()
    private var storageError=false
    private var screenWake:PowerManager.WakeLock?=null
    private var screenWakeOwner=""
    private var activeVibration=""
    private var lastIssued=-10_000L
    private var pendingPresentation:String?=null
    fun subscribe(observer:(JSONObject)->Unit){observers.add(observer)}
    fun unsubscribe(observer:(JSONObject)->Unit){observers.remove(observer)}
    fun initialize(ctx:Context){
        context=ctx.applicationContext
        file=AtomicFile(File(context.filesDir,"agent-reminders/ledger.json"))
        val old=File(context.filesDir,"rfcomm-probe/reminders.json")
        try {
            val hasJournal=file.baseFile.exists() || File(file.baseFile.path+".bak").exists() || File(file.baseFile.path+".new").exists()
            val saved=if(hasJournal)String(file.readFully(),Charsets.UTF_8) else if(old.exists())old.readText() else "{}"
            require(saved.length<=512_000)
            val value=JSONObject(saved)
            value.keys().forEach { id ->
                val row=value.getJSONObject(id)
                if(row.optString("status")=="accepted")row.put("status","outcome_unknown_after_restart")
                journal.restore(ReminderJournal.Entry(id,row.optString("payloadHash").takeIf{it.isNotBlank()},row.optLong("expiresAtMs"),row))
            }
            if(saved!="{}")persist()
        }catch(_:Exception){storageError=true}
        (context as Application).registerActivityLifecycleCallbacks(object:Application.ActivityLifecycleCallbacks{
            override fun onActivityResumed(activity:Activity){
                pendingPresentation?.let{id -> if(!activity.isFinishing)launchPage(id,activity)}
            }
            override fun onActivityCreated(activity:Activity,state:Bundle?){}
            override fun onActivityStarted(activity:Activity){}
            override fun onActivityPaused(activity:Activity){}
            override fun onActivityStopped(activity:Activity){}
            override fun onActivitySaveInstanceState(activity:Activity,state:Bundle){}
            override fun onActivityDestroyed(activity:Activity){}
        })
    }
    private fun persist(){
        val value=JSONObject();journal.entries().forEach { value.put(it.id,it.value) }
        file.baseFile.parentFile?.mkdirs()
        val output=file.startWrite()
        try{output.write(value.toString().toByteArray(Charsets.UTF_8));file.finishWrite(output)}
        catch(e:Exception){file.failWrite(output);throw e}
    }
    private fun emit(row:JSONObject){observers.forEach { try{it(JSONObject(row.toString()).put("event","reminder_receipt"))}catch(_:Exception){} }}
    fun receipt(id:String):JSONObject?=journal.get(id)?.value?.let{JSONObject(it.toString())}
    fun recent():List<JSONObject> = journal.entries().map{JSONObject(it.value.toString())}
        .filter{it.optString("title").isNotBlank() && it.optString("body").isNotBlank()}
        .sortedByDescending{it.optLong("receivedAtMs")}
    fun unreadCount()=journal.entries().count{!it.value.has("acknowledgedAtMs") && it.value.optString("title").isNotBlank() && it.value.optString("body").isNotBlank()}
    data class DeleteResult(val count:Int=0,val success:Boolean=true)
    fun delete(ids:Set<String>):DeleteResult {
        if(storageError)return DeleteResult(success=false)
        val readable=ids.filterTo(mutableSetOf()){journal.get(it)?.value?.let{row ->
            !row.has("deletedAtMs") && row.optString("title").isNotBlank() && row.optString("body").isNotBlank()}==true}
        val deleted=try {
            journal.redact(readable,{entry ->
                // No title, body or effect payload survives deletion. Keeping the fingerprint
                // prevents retries of this ID from starting the reminder again.
                JSONObject().put("status","deleted").put("reminderId",entry.id)
                    .put("expiresAtMs",entry.expiresAtMs).put("deletedAtMs",System.currentTimeMillis())
                    .apply{entry.fingerprint?.let{put("payloadHash",it)}}
            },{persist()})
        }catch(_:Exception){storageError=true;return DeleteResult(success=false)}
        deleted.forEach { entry ->
            if(pendingPresentation==entry.id)pendingPresentation=null
            context.getSystemService(NotificationManager::class.java).cancel(entry.id,4)
            ReminderAudio.stop(entry.id,"deleted")
            if(activeVibration==entry.id){context.getSystemService(Vibrator::class.java).cancel();activeVibration=""}
            if(screenWakeOwner==entry.id)releaseWake()
            ReminderActivity.close(entry.id,includingHistory=true)
            emit(entry.value)
        }
        return DeleteResult(deleted.size)
    }
    private fun integer(p:JSONObject,key:String,default:Long?=null):Long {
        if(!p.has(key))return requireNotNull(default){"missing $key"}
        val n=p.get(key);require(n is Int || n is Long){"$key must be an integer"};return (n as Number).toLong()
    }
    private fun flag(p:JSONObject,key:String,default:Boolean):Boolean {
        if(!p.has(key))return default
        require(p.get(key) is Boolean){"$key must be boolean"};return p.getBoolean(key)
    }
    private fun parse(p:JSONObject):Pair<ReminderSpec,String> {
        val allowed=setOf("reminderId","title","body","template","vibrate","screen","strength","vibration","screenSeconds","expiresAtMs","ttlMs","sound","soundSource")
        require(p.keys().asSequence().all{it in allowed}){"unknown reminder parameter"}
        for(k in listOf("reminderId","title","body"))require(p.get(k) is String){"$k must be text"}
        val template=p.optString("template","strong")
        require(template in setOf("notification_only","silent","light","standard","strong")){"invalid template"}
        val strength=p.optString("strength",if(template in setOf("light","standard","strong"))template else "strong")
        val base=ReminderVibration.profile(strength)
        val custom=if(p.has("vibration"))p.getJSONObject("vibration") else JSONObject()
        require(custom.keys().asSequence().all{it in setOf("amplitude","pulseMs","gapMs","count")}){"unknown vibration parameter"}
        fun boundedInt(key:String,default:Long,low:Long,high:Long):Long=integer(custom,key,default).also{require(it in low..high){"invalid $key"}}
        val pattern=ReminderVibration(boundedInt("amplitude",base.amplitude.toLong(),1,255).toInt(),boundedInt("pulseMs",base.pulseMs,20,1000),
            boundedInt("gapMs",base.gapMs,0,2000),boundedInt("count",2,1,8).toInt())
        require(p.has("ttlMs")!=p.has("expiresAtMs")){"provide ttlMs or expiresAtMs"}
        val now=System.currentTimeMillis()
        val expiry=if(p.has("ttlMs"))now+integer(p,"ttlMs").also{require(it in 1..300_000)} else integer(p,"expiresAtMs")
        val spec=ReminderSpec(p.getString("reminderId"),p.getString("title"),p.getString("body"),
            flag(p,"vibrate",template !in setOf("notification_only","silent")),flag(p,"screen",template!="notification_only"),expiry,strength,pattern,
            integer(p,"screenSeconds",15).also{require(it in 5..30)}.toInt(),flag(p,"sound",false),
            if(p.has("soundSource"))p.get("soundSource").also{require(it is String){"soundSource must be text"}} as String else "system")
        ReminderSoundPolicy.validateSource(spec.soundSource)
        // Deadline changes never make the same ID deliver twice. Compare content/effects.
        val effects=mutableListOf<Any>(spec.title,spec.body,spec.vibrate,spec.screen,pattern.amplitude,pattern.pulseMs,pattern.gapMs,pattern.count,spec.screenSeconds)
        // Keep old no-sound fingerprints intact, including retained/deleted IDs.
        if(spec.sound)effects.addAll(listOf(true,spec.soundSource))
        val canonical=JSONArray(effects).toString()
        val hash=MessageDigest.getInstance("SHA-256").digest(canonical.toByteArray(Charsets.UTF_8)).joinToString(""){"%02x".format(it.toInt() and 255)}
        return spec to hash
    }
    fun execute(request:JSONObject):JSONObject {
        if(!AgentInteraction.enabled())return JSONObject().put("status","interaction_disabled")
        if(storageError)return JSONObject().put("status","reminder_storage_error")
        return try {
            val params=request.optJSONObject("params")?:JSONObject()
            when(request.optString("op")){
                "reminder_status" -> {
                    require(params.keys().asSequence().toSet()==setOf("reminderId"))
                    receipt(params.getString("reminderId"))?:JSONObject().put("status","not_found")
                }
                "remind" -> issue(params)
                else -> JSONObject().put("status","unknown_operation")
            }
        }catch(_:java.io.IOException){storageError=true;cancelEffects();JSONObject().put("status","reminder_storage_error")}
        catch(e:Exception){JSONObject().put("status","invalid_request").put("message",e.message)}
    }
    private fun issue(params:JSONObject):JSONObject {
        val (spec,hash)=parse(params)
        journal.get(spec.id)?.let {
            return if(it.fingerprint==null || it.fingerprint==hash)JSONObject(it.value.toString()).put("duplicate",true)
                else JSONObject().put("status","reminder_id_conflict").put("reminderId",spec.id)
        }
        ReminderPolicy.rejection(spec)?.let{return it.put("reminderId",spec.id)}
        spec.validate(System.currentTimeMillis())
        if(SystemClock.elapsedRealtime()-lastIssued<2000)return JSONObject().put("status","rate_limited").put("retryAfterMs",2000-(SystemClock.elapsedRealtime()-lastIssued))
        if(activeVibration.isNotEmpty())return JSONObject().put("status","busy")
        val nm=context.getSystemService(NotificationManager::class.java)
        val system=ReminderSystemState.read(context)
        val filter=system.input.filter;val permitted=system.decision.effectsAllowed
        val locked=context.getSystemService(KeyguardManager::class.java).isDeviceLocked
        val interactive=Probe.screen(context)
        val row=JSONObject().put("status","accepted").put("reminderId",spec.id).put("payloadHash",hash).put("title",spec.title).put("body",spec.body)
            .put("receivedAtMs",System.currentTimeMillis()).put("expiresAtMs",spec.expiresAtMs).put("template",params.optString("template","strong"))
            .put("vibrationRequested",spec.vibrate).put("screenRequested",spec.screen).put("screenSeconds",spec.screenSeconds)
            .put("soundRequested",spec.sound).put("soundSource",spec.soundSource).put("soundIssued",false)
            .put("interruptionFilter",filter).put("suppressedByDnd",system.decision.suppressedByDnd)
            .put("suppressedBySystem",!permitted).put("systemAlertPolicy",system.json.put("soundRequested",spec.sound).put("soundIssued",false))
            .put("screenSuppressedByLock",locked).put("vibrationIssued",false).put("screenLaunchRequested",false).put("strength",spec.strength)
            .put("screenInteractiveAtReceipt",interactive)
            .put("requestedVibration",JSONObject().put("amplitude",spec.vibration.amplitude).put("pulseMs",spec.vibration.pulseMs).put("gapMs",spec.vibration.gapMs).put("count",spec.vibration.count).put("totalMs",spec.vibration.totalMs()))
        val reserve=journal.reserve(ReminderJournal.Entry(spec.id,hash,spec.expiresAtMs,row),System.currentTimeMillis())
        if(reserve!="reserved")return JSONObject().put("status",reserve)
        try{persist()}catch(e:Exception){journal.remove(spec.id);throw e}
        lastIssued=SystemClock.elapsedRealtime()
        try {
            val read=Intent(context,ReminderActivity::class.java).setData(android.net.Uri.parse("heartradio://history/${spec.id}"))
                .putExtra("reminder_id",spec.id).putExtra("read_history",true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            val open=PendingIntent.getActivity(context,spec.id.hashCode(),read,PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val wake=spec.screen && permitted && !locked
            // Read modes immediately before selecting an audible channel. System policy
            // remains authoritative if a mode changes between this read and notify().
            val sound=runCatching{ReminderAudio.plan(context,spec,ReminderSystemState.read(context),wake)}
                .getOrElse{ReminderAudio.Plan(reason="sound_setup_error")}
            val channel=if(wake)"agent_reminders_v1" else "agent_reminders_quiet_v1"
            nm.createNotificationChannel(NotificationChannel(channel,if(channel=="agent_reminders_v1")"Agent 提醒" else "Agent 静默通知",
                if(channel=="agent_reminders_v1")NotificationManager.IMPORTANCE_HIGH else NotificationManager.IMPORTANCE_LOW).apply{enableVibration(false);setSound(null,null)})
            val notification=Notification.Builder(context,channel).setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(spec.title).setContentText(spec.body).setStyle(Notification.BigTextStyle().bigText(spec.body))
                .setCategory(Notification.CATEGORY_REMINDER)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .setContentIntent(open).setAutoCancel(true).setTimeoutAfter((spec.expiresAtMs-System.currentTimeMillis()).coerceAtLeast(1))
            nm.notify(spec.id,4,notification.build())
            ReminderAudio.receipt(row,sound)
            if(sound.allowed)ReminderAudio.play(context,spec,wake,canContinue={
                journal.get(spec.id)?.value?.let{!it.has("deletedAtMs") && !it.has("cancelledAtMs") && !it.has("acknowledgedAtMs")}==true
            }){state,reason ->
                if(journal.get(spec.id)?.value===row && !row.has("deletedAtMs")){
                    row.put("soundStatus",state).put("soundEventSeq",row.optInt("soundEventSeq")+1)
                    val field=when(state){"started"->"soundStartedAtMs";"completed"->"soundCompletedAtMs";else->"soundStoppedAtMs"}
                    row.put(field,System.currentTimeMillis())
                    if(state=="started")row.put("soundIssued",true)
                    reason?.let{row.put(if(state=="suppressed")"soundSuppressedReason" else "soundStopReason",it)}
                    try{persist();emit(row)}catch(_:Exception){storageError=true;cancelEffects()}
                }
            }
            if(permitted && spec.vibrate){
                val vibrator=context.getSystemService(Vibrator::class.java);val control=vibrator.hasAmplitudeControl()
                row.put("amplitudeControl",control).put("vibratorAvailable",vibrator.hasVibrator())
                if(vibrator.hasVibrator()){
                    val pattern=spec.vibration
                    vibrator.vibrate(if(control)VibrationEffect.createWaveform(pattern.timings(),pattern.amplitudes(),-1) else VibrationEffect.createWaveform(pattern.timings(),-1),
                        ReminderSystemState.vibrationAttributes(system))
                    activeVibration=spec.id
                    row.put("vibrationIssued",true).put("appliedVibration",JSONObject(row.getJSONObject("requestedVibration").toString()).put("amplitude",if(control)pattern.amplitude else JSONObject.NULL))
                    handler.postDelayed({if(activeVibration==spec.id){vibrator.cancel();activeVibration="";mark(spec.id,"vibration_complete")}},pattern.totalMs())
                }
            }
            if(permitted && spec.screen && !locked){
                var restoredTask=false
                if(ReminderPresentation.useExistingTask(Build.VERSION.SDK_INT,spec.screen,permitted,locked)){
                    // API 27 queues a background start shortly after returning home. The
                    // public AppTask API restores only our own existing task before launch.
                    pendingPresentation=spec.id
                    if(!interactive)wakeScreen(row)
                    try{
                        val tasks=context.getSystemService(ActivityManager::class.java).appTasks.filter{
                            it.taskInfo.baseIntent.component?.packageName==context.packageName}
                        val task=tasks.firstOrNull{it.taskInfo.baseIntent.component?.className==MainActivity::class.java.name}?:tasks.firstOrNull()
                        task?.let{it.moveToFront();restoredTask=true;row.put("appTaskFrontRequestedAtMs",System.currentTimeMillis())}
                    }catch(e:Exception){row.put("appTaskFrontError",e.javaClass.simpleName)}
                }
                if(restoredTask){
                    // While waking, the restored task must finish resuming before an
                    // Activity launch is foreground-owned. Fall back once if it cannot.
                    handler.postDelayed({if(pendingPresentation==spec.id)launchPage(spec.id,context)},500)
                }else{
                    pendingPresentation=spec.id;launchPage(spec.id,context)
                }
            }
            row.put("status",if(permitted)"issued" else if(system.decision.suppressedByDnd)"notification_only_dnd" else "notification_only_system_restricted")
        }catch(e:Exception){row.put("status","delivery_error").put("deliveryError",e.javaClass.simpleName);cancelEffects()}
        persist();emit(row)
        val delay=(spec.expiresAtMs-System.currentTimeMillis()).coerceAtLeast(1)
        handler.postDelayed({
            context.getSystemService(NotificationManager::class.java).cancel(spec.id,4)
            ReminderAudio.stop(spec.id,"expired")
            if(activeVibration==spec.id)cancelEffects()
            mark(spec.id,"expired");ReminderActivity.close(spec.id)
        },delay)
        return JSONObject(row.toString())
    }
    fun mark(id:String,state:String){
        val row=journal.get(id)?.value?:return
        if(row.has("deletedAtMs"))return
        val field=when(state){"acknowledged"->"acknowledgedAtMs";"displayed"->"displayedAtMs";"activity_created"->"activityCreatedAtMs";"activity_resumed"->"activityResumedAtMs";"history_displayed"->"historyDisplayedAtMs";"vibration_complete"->"vibrationCompletedAtMs";"expired"->"expiredAtMs";else->"screenClosedAtMs"}
        if(row.has(field))return
        row.put(field,System.currentTimeMillis())
        // Lifecycle diagnostics join the next business commit (display, close, ack, expiry).
        // Accepted/issued effects and every business transition keep their synchronous barrier.
        if(state=="activity_created" || state=="activity_resumed"){
            Probe.log(context,"reminder_presentation",mapOf("reminderId" to id,"milestone" to state,"atMs" to row.optLong(field)))
            emit(row);return
        }
        if(state=="displayed")releaseWake()
        if(state=="acknowledged"){
            context.getSystemService(NotificationManager::class.java).cancel(id,4)
            ReminderAudio.stop(id,"acknowledged")
            if(activeVibration==id)cancelEffects()
        }
        try{persist();emit(row)}catch(_:Exception){storageError=true;cancelEffects()}
    }
    fun recordLayout(id:String,layout:JSONObject){
        val row=journal.get(id)?.value?:return
        if(row.has("deletedAtMs"))return
        row.put("layout",layout)
        // Layout is diagnostic, never a delivery/acknowledgement or deduplication barrier.
        // Keep it in the live receipt and include it in the next unchanged business commit.
        Probe.log(context,"reminder_layout",mapOf("reminderId" to id,"layout" to JSONObject(layout.toString())))
        emit(row)
    }
    fun cancelAll(reason:String){
        pendingPresentation=null;cancelEffects();handler.removeCallbacksAndMessages(null);ReminderActivity.close(null)
        val changed=mutableListOf<JSONObject>()
        journal.entries().forEach { entry ->
            val row=entry.value
            if(!row.has("deletedAtMs") && !row.has("acknowledgedAtMs") && !row.has("cancelledAtMs") && entry.expiresAtMs>System.currentTimeMillis()){
                context.getSystemService(NotificationManager::class.java).cancel(entry.id,4)
                row.put("cancelledAtMs",System.currentTimeMillis()).put("cancelReason",reason);changed.add(row)
            }
        }
        if(changed.isNotEmpty())try{persist();changed.forEach{emit(it)}}catch(_:Exception){storageError=true}
    }
    private fun releaseWake(){screenWake?.let{if(it.isHeld)it.release()};screenWake=null;screenWakeOwner=""}
    private fun launchPage(id:String,owner:Context){
        pendingPresentation=null
        val row=journal.get(id)?.value?:return
        if(row.has("deletedAtMs"))return
        if(!ReminderPresentation.canOpen(false,context.getSystemService(KeyguardManager::class.java).isDeviceLocked,
                AgentInteraction.enabled(),row.has("cancelledAtMs"),row.has("acknowledgedAtMs"),row.optLong("expiresAtMs"),System.currentTimeMillis()))return
        val page=Intent(context,ReminderActivity::class.java).setData(android.net.Uri.parse("heartradio://reminder/$id"))
            .putExtra("reminder_id",id).putExtra("wake_screen",true)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_NO_ANIMATION)
        try{
            owner.startActivity(page)
            row.put("screenLaunchRequested",true).put("screenLaunchRequestedAtMs",System.currentTimeMillis())
                .put("screenLaunchFromActivity",owner is Activity)
        }catch(e:Exception){row.put("screenLaunchError",e.javaClass.simpleName)}
        try{persist();emit(row)}catch(_:Exception){storageError=true;cancelEffects()}
    }
    @Suppress("DEPRECATION")
    private fun wakeScreen(row:JSONObject){
        releaseWake()
        val lock=context.getSystemService(PowerManager::class.java).newWakeLock(PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP,"HeartRadio:reminder-screen")
        val id=row.getString("reminderId")
        lock.setReferenceCounted(false);screenWake=lock;screenWakeOwner=id;lock.acquire(2000);row.put("screenWakeRequested",true)
        handler.postDelayed({if(screenWakeOwner==id)releaseWake()},1500)
    }
    private fun cancelEffects(){
        if(activeVibration.isNotEmpty())context.getSystemService(Vibrator::class.java).cancel()
        ReminderAudio.stop(reason="effects_cancelled")
        activeVibration="";releaseWake()
    }
}
