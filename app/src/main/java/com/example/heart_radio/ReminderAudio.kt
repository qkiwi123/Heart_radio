package com.example.heart_radio

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.net.Uri
import android.os.Handler
import android.os.Looper
import org.json.JSONObject

/** Bounded, one-shot system-tone playback. Visual notifications stay silent. */
object ReminderAudio {
    private val handler=Handler(Looper.getMainLooper())
    private var player:MediaPlayer?=null
    private var owner=""
    private var audio:AudioManager?=null
    private var focus:AudioFocusRequest?=null
    private var observer:((String,String?)->Unit)?=null
    private fun attributes()=AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION)
        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
    data class Plan(val channel:NotificationChannel?=null,val reason:String?=null) {
        val allowed get()=channel!=null && reason==null
    }
    // Future custom assets belong behind this resolver; never accept a remote path/URL.
    private fun sourceUri(source:String):Uri {
        ReminderSoundPolicy.validateSource(source)
        return RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
    }
    fun plan(context:Context,spec:ReminderSpec,system:ReminderSystemState.Snapshot,wake:Boolean,createChannel:Boolean=true):Plan {
        val snapshot=system.json
        val reason=ReminderSoundPolicy.suppression(spec.sound,system.decision.soundAllowedByMode,
            if(snapshot.isNull("notificationVolume"))null else snapshot.getInt("notificationVolume"),
            if(snapshot.isNull("notificationStreamMuted"))null else snapshot.getBoolean("notificationStreamMuted"))
        if(reason!=null)return Plan(reason=reason)
        val manager=context.getSystemService(NotificationManager::class.java)
        if(!manager.areNotificationsEnabled())return Plan(reason="app_notifications_disabled")
        val id=if(wake)"agent_reminders_sound_v1" else "agent_reminders_sound_passive_v1"
        if(createChannel)manager.createNotificationChannel(NotificationChannel(id,if(wake)"提醒声音与显示" else "提醒声音",
            if(wake)NotificationManager.IMPORTANCE_HIGH else NotificationManager.IMPORTANCE_DEFAULT).apply {
            enableVibration(false);setSound(sourceUri(spec.soundSource),attributes())
        })
        // Existing channel settings always win; never delete or recreate to override them.
        val channel=manager.getNotificationChannel(id)?:return Plan(reason="sound_channel_unavailable")
        if(channel.importance<NotificationManager.IMPORTANCE_DEFAULT || channel.sound==null)
            return Plan(reason="sound_channel_muted")
        if(channel.sound==RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION) &&
            RingtoneManager.getActualDefaultRingtoneUri(context,RingtoneManager.TYPE_NOTIFICATION)==null)
            return Plan(reason="system_default_tone_silent")
        return Plan(channel)
    }
    fun receipt(row:JSONObject,plan:Plan) {
        row.put("soundIssued",false).put("soundMethod","system_tone_player").put("soundEventSeq",0)
        row.put("soundStatus",if(plan.allowed)"preparing" else if(plan.reason=="not_requested")"not_requested" else "suppressed")
        plan.reason?.let{row.put("soundSuppressedReason",it)}
        plan.channel?.let{row.put("soundChannel",it.id)}
        if(plan.allowed)row.put("soundPreparingAtMs",System.currentTimeMillis())
    }

    fun play(context:Context,spec:ReminderSpec,wake:Boolean,canContinue:()->Boolean,event:(String,String?)->Unit) {
        stop(reason="superseded")
        val current=MediaPlayer();player=current;owner=spec.id;observer=event
        fun guard():String? {
            if(!canContinue() || !AgentInteraction.enabled() || !ReminderPolicy.limits().soundEnabled || System.currentTimeMillis()>=spec.expiresAtMs)
                return "reminder_no_longer_active"
            return plan(context,spec,ReminderSystemState.read(context),wake,false).reason
        }
        try {
            val ready=plan(context,spec,ReminderSystemState.read(context),wake,false)
            ready.reason?.let{stop(spec.id,it,"suppressed");return}
            var uri=requireNotNull(ready.channel?.sound)
            if(uri==RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION))
                uri=requireNotNull(RingtoneManager.getActualDefaultRingtoneUri(context,RingtoneManager.TYPE_NOTIFICATION))
            current.setAudioAttributes(attributes())
            current.setOnErrorListener { _,what,extra ->
                if(player===current)stop(spec.id,"player_error_${what}_$extra","failed");true
            }
            current.setOnCompletionListener { if(player===current)stop(spec.id,null,"completed") }
            current.setOnPreparedListener {
                if(player!==current)return@setOnPreparedListener
                try {
                    guard()?.let{stop(spec.id,it,"suppressed");return@setOnPreparedListener}
                    current.isLooping=false
                    val manager=context.getSystemService(AudioManager::class.java);audio=manager
                    val request=AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
                        .setAudioAttributes(attributes()).setOnAudioFocusChangeListener({change ->
                            if(change<0 && player===current)stop(spec.id,"audio_focus_lost")
                        },handler).build()
                    focus=request
                    if(manager.requestAudioFocus(request)!=AudioManager.AUDIOFOCUS_REQUEST_GRANTED){
                        stop(spec.id,"audio_focus_denied","suppressed");return@setOnPreparedListener
                    }
                    guard()?.let{stop(spec.id,it,"suppressed");return@setOnPreparedListener}
                    handler.removeCallbacksAndMessages(null)
                    current.start();event("started",null)
                    handler.postDelayed({if(player===current)stop(spec.id,"duration_limit")},minOf(3000L,(spec.expiresAtMs-System.currentTimeMillis()).coerceAtLeast(1)))
                    val monitor=object:Runnable {
                        override fun run(){
                            if(player!==current)return
                            val reason=runCatching{guard()}.getOrElse{"audio_state_unknown"}
                            if(reason!=null)stop(spec.id,reason) else handler.postDelayed(this,200)
                        }
                    }
                    handler.postDelayed(monitor,200)
                }catch(_:Exception){if(player===current)stop(spec.id,"player_start_failed","failed")}
            }
            current.setDataSource(context,uri);current.prepareAsync()
            handler.postDelayed({if(player===current)stop(spec.id,"prepare_timeout","failed")},3000)
        }catch(_:Exception){stop(spec.id,"sound_setup_error","failed")}
    }
    fun stop(id:String?=null,reason:String?="cancelled",state:String="stopped") {
        if(player==null || (id!=null && owner!=id))return
        val old=player;val callback=observer;val manager=audio;val request=focus
        player=null;owner="";observer=null;audio=null;focus=null;handler.removeCallbacksAndMessages(null)
        runCatching{old?.release()}
        if(manager!=null && request!=null)runCatching{manager.abandonAudioFocusRequest(request)}
        callback?.invoke(state,reason)
    }
}
