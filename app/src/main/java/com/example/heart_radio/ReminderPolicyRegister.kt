package com.example.heart_radio

data class ReminderLimits(val vibrationEnabled:Boolean=true,val maxVibrationMs:Long=6000,
                          val wakeEnabled:Boolean=true,val maxScreenSeconds:Int=30,val soundEnabled:Boolean=false){
    fun validate(){require(maxVibrationMs in 1000..10000&&maxScreenSeconds in 5..30)}
    fun violation(vibrate:Boolean,totalMs:Long,screen:Boolean,screenSeconds:Int,sound:Boolean=false):String?=when{
        vibrate&&!vibrationEnabled->"vibration_disabled_by_user"
        vibrate&&totalMs>maxVibrationMs->"vibration_exceeds_user_limit"
        screen&&!wakeEnabled->"wake_disabled_by_user"
        screen&&screenSeconds>maxScreenSeconds->"screen_exceeds_user_limit"
        sound&&!soundEnabled->"sound_disabled_by_user"
        else->null
    }
}
data class PolicyEvent(val seq:Long,val seen:Long,val id:String,val value:ReminderLimits)
data class PolicySnapshot(val pc:PolicyEvent?,val watch:PolicyEvent?)

class ReminderPolicyRegister(private val author:String){
    init{require(author in setOf("pc","watch"))}
    var pc:PolicyEvent?=null;private set
    var watch:PolicyEvent?=null;private set
    fun snapshot()=PolicySnapshot(pc,watch)
    fun fingerprint()="${pc?.seq?:0}-${pc?.id?:""}:${watch?.seq?:0}-${watch?.id?:""}"
    fun copy()=ReminderPolicyRegister(author).also{it.pc=pc;it.watch=watch}
    fun effective():ReminderLimits{
        val p=pc;val w=watch
        if(p==null)return w?.value?:ReminderLimits()
        if(w==null||p.seen>=w.seq)return p.value
        if(w.seen>=p.seq)return w.value
        return ReminderLimits(p.value.vibrationEnabled&&w.value.vibrationEnabled,minOf(p.value.maxVibrationMs,w.value.maxVibrationMs),
            p.value.wakeEnabled&&w.value.wakeEnabled,minOf(p.value.maxScreenSeconds,w.value.maxScreenSeconds),p.value.soundEnabled&&w.value.soundEnabled)
    }
    fun edit(value:ReminderLimits){
        value.validate();val own=if(author=="pc")pc else watch;val peer=if(author=="pc")watch else pc
        val next=PolicyEvent((own?.seq?:0)+1,peer?.seq?:0,java.util.UUID.randomUUID().toString().replace("-",""),value)
        require(next.seq<=Int.MAX_VALUE)
        if(author=="pc")pc=next else watch=next
    }
    fun merge(s:PolicySnapshot){
        validate(s)
        fun newer(old:PolicyEvent?,next:PolicyEvent?):PolicyEvent?{
            if(next==null)return old
            if(old==null||next.seq>old.seq)return next
            if(next.seq==old.seq)require(next==old){"Conflicting policy identity"}
            return old
        }
        val next=PolicySnapshot(newer(pc,s.pc),newer(watch,s.watch));validate(next);pc=next.pc;watch=next.watch
    }
    private fun validate(s:PolicySnapshot){
        listOfNotNull(s.pc,s.watch).forEach{require(it.seq in 1..Int.MAX_VALUE.toLong()&&it.seen in 0..Int.MAX_VALUE.toLong()&&it.id.matches(Regex("[0-9a-f]{32}")));it.value.validate()}
        val p=s.pc;val w=s.watch
        require(p==null||p.seen<=(w?.seq?:0));require(w==null||w.seen<=(p?.seq?:0))
        require(p==null||w==null||p.seen<w.seq||w.seen<p.seq)
    }
}
