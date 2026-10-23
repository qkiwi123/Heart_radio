package com.example.heart_radio

/** An unchanged network snapshot cannot retry a failed or still-pending sensor start. */
class BroadcastApplyGuard {
    private var applied=""
    fun invalidate(){applied=""}
    fun next(version:String,enabled:Boolean,running:Boolean):Boolean? {
        if(version==applied)return null
        applied=version
        return enabled.takeIf{it!=running}
    }
}
