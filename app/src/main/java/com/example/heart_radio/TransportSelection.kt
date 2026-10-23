package com.example.heart_radio

/** A route belongs to an authenticated session. Old cleanup cannot clear its replacement. */
class TransportSelection {
    var transport: String? = null; private set
    var session: String? = null; private set
    fun select(mode: String, candidate: String, identity: String): Boolean {
        require(identity.matches(Regex("[0-9a-f]{32}")))
        if(candidate !in allowed(mode))return false
        transport=candidate;session=identity;return true
    }
    fun release(candidate: String, identity: String): Boolean {
        if(!owns(candidate,identity))return false
        transport=null;session=null;return true
    }
    fun owns(candidate:String,identity:String)=transport==candidate&&session==identity
    companion object {
        val modes=listOf("auto_bluetooth","auto_wifi","rfcomm","wifi")
        fun allowed(mode:String):Set<String> = when(mode){
            "auto_bluetooth","auto_wifi"->setOf("rfcomm","wifi")
            "rfcomm","wifi"->setOf(mode)
            else->throw IllegalArgumentException("Unknown connection mode")
        }
        fun label(mode:String)=when(mode){"auto_bluetooth"->"自动·蓝牙优先";"auto_wifi"->"自动·Wi-Fi优先";"rfcomm"->"仅蓝牙";else->"仅Wi-Fi"}
    }
}
