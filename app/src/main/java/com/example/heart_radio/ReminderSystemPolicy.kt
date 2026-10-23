package com.example.heart_radio

/** OS notification filters and the watch's visible silent/DND switches are different inputs. */
object ReminderSystemPolicy {
    data class Input(val filter:Int,val ringerMode:Int?,val oppoWatch:Boolean=false,
                     val vendorSilent:Int?=null,val vendorDnd:Int?=null)
    data class Decision(val mode:String,val label:String,val effectsAllowed:Boolean,
                        val suppressedByDnd:Boolean=false,val silentCompatibility:Boolean=false,
                        val vibrationAudioUsage:Int=5,val soundAllowedByMode:Boolean=false,val reason:String)
    fun decide(value:Input):Decision {
        // Explicit DND wins even if other OS fields are temporarily inconsistent.
        if(value.oppoWatch && value.vendorDnd==1)
            return Decision("do_not_disturb","勿扰模式",false,suppressedByDnd=true,reason="oppo_disturb_enabled")
        if(value.oppoWatch && value.vendorSilent==1 && value.vendorDnd==0 && value.filter in setOf(1,4))
            return Decision("silent","静音模式",true,silentCompatibility=value.filter==4,
                vibrationAudioUsage=if(value.filter==4)4 else 5,reason="oppo_silent_without_disturb")
        if(value.filter==1) {
            if(value.oppoWatch && value.vendorSilent==1)
                return Decision("silent","静音模式",true,reason="oppo_silent_unfiltered")
            return when(value.ringerMode) {
                0 -> Decision("silent","系统铃声静音",true,reason="ringer_silent")
                1 -> Decision("vibrate","振动模式",true,reason="ringer_vibrate")
                else -> Decision("normal","正常模式",true,soundAllowedByMode=value.ringerMode==2,reason="filter_all")
            }
        }
        if(!value.oppoWatch && value.filter in setOf(2,3,4))
            return Decision("do_not_disturb","系统勿扰",false,suppressedByDnd=true,reason="platform_interruption_filter")
        return Decision("system_restricted",if(value.filter==0)"系统状态未知" else "系统通知限制",false,
            reason=if(value.filter==0)"filter_unknown" else "unclassified_interruption_filter")
    }
}
