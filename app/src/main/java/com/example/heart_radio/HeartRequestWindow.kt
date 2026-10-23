package com.example.heart_radio

/** Receipt deadline only. Raw source timestamps remain unmodified and unverified. */
class HeartRequestWindow(val beginNs:Long,val seconds:Int){
    init{require(seconds in 1..15)}
    val reading=ReadingState().also{it.reset(beginNs)}
    fun expired(nowNs:Long)=nowNs<beginNs||nowNs-beginNs>=seconds*1_000_000_000L
    fun remainingMs(nowNs:Long):Long = if(expired(nowNs))0 else (seconds*1_000_000_000L-(nowNs-beginNs)+999_999L)/1_000_000L
    fun timeoutStatus()=if(reading.accuracy==-1)"no_contact" else if(reading.callbacks>0)"no_valid_data" else "timeout"
}
