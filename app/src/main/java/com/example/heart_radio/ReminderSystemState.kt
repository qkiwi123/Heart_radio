package com.example.heart_radio

import android.app.NotificationManager
import android.content.Context
import android.media.AudioManager
import android.media.AudioAttributes
import android.os.Build
import android.provider.Settings
import org.json.JSONObject

/** Read-only mode snapshot. Playback is separately owned by the reminder notification. */
object ReminderSystemState {
    data class Snapshot(val input:ReminderSystemPolicy.Input,val decision:ReminderSystemPolicy.Decision,val json:JSONObject)
    fun vibrationAttributes(snapshot:Snapshot):AudioAttributes = AudioAttributes.Builder().setUsage(
        if(snapshot.decision.silentCompatibility)AudioAttributes.USAGE_ALARM else AudioAttributes.USAGE_NOTIFICATION).build()
    fun read(context:Context):Snapshot {
        val filter=runCatching{context.getSystemService(NotificationManager::class.java).currentInterruptionFilter}.getOrDefault(0)
        val audio=context.getSystemService(AudioManager::class.java)
        val ringer=runCatching{audio.ringerMode}.getOrNull()
        val supported=Build.VERSION.SDK_INT==27 && Build.MODEL=="OW20W2" && Build.BRAND.equals("OPPO",true)
        fun vendor(key:String)=if(supported)runCatching{Settings.System.getString(context.contentResolver,key)?.toIntOrNull()?.takeIf{it in 0..1}}.getOrNull() else null
        val input=ReminderSystemPolicy.Input(filter,ringer,supported,vendor("mute_mode"),vendor("disturb_mode"))
        val decision=ReminderSystemPolicy.decide(input)
        val volume=runCatching{audio.getStreamVolume(AudioManager.STREAM_NOTIFICATION)}.getOrNull()
        val muted=runCatching{audio.isStreamMute(AudioManager.STREAM_NOTIFICATION)}.getOrNull()
        val data=JSONObject().put("version",1).put("mode",decision.mode).put("modeLabel",decision.label)
            .put("source",if(supported)"oppo_ow20w2_api27" else "android_public_api")
            .put("interruptionFilter",filter).put("ringerMode",ringer?:JSONObject.NULL)
            .put("vendorSilent",input.vendorSilent?:JSONObject.NULL).put("vendorDnd",input.vendorDnd?:JSONObject.NULL)
            .put("effectsAllowed",decision.effectsAllowed).put("suppressedByDnd",decision.suppressedByDnd)
            .put("silentCompatibility",decision.silentCompatibility).put("vibrationAudioUsage",decision.vibrationAudioUsage)
            .put("reason",decision.reason).put("notificationVolume",volume?:JSONObject.NULL)
            .put("notificationStreamMuted",muted?:JSONObject.NULL)
            .put("soundAllowedByMode",decision.soundAllowedByMode)
            .put("futureSoundAllowed",decision.soundAllowedByMode && volume!=null && volume>0 && muted==false)
            .put("soundImplemented",true)
        return Snapshot(input,decision,data)
    }
}
