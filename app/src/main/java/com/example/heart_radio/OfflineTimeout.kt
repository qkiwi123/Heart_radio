package com.example.heart_radio

/** Counts continuous absence of an authenticated, selected application connection. */
class OfflineTimeout(private val limitMs:Long=600_000L) {
    private var waitingSince:Long?=null
    private var fired=false
    fun start(now:Long){waitingSince=now;fired=false}
    fun remaining(now:Long,connected:Boolean):Long {
        if(connected){waitingSince=null;return limitMs}
        val start=waitingSince?:now.also{waitingSince=it}
        return (limitMs-(now-start).coerceAtLeast(0)).coerceAtLeast(0)
    }
    fun expired(now:Long,connected:Boolean):Boolean {
        if(fired)return false
        if(remaining(now,connected)>0)return false
        fired=true;return true
    }
}
