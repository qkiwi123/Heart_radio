package com.example.heart_radio

/** Bounded plain text; no executable content or implicit retries. */
data class ReminderSpec(val id: String, val title: String, val body: String,
                        val vibrate: Boolean, val screen: Boolean, val expiresAtMs: Long,
                        val strength: String = "strong", val vibration: ReminderVibration = ReminderVibration.profile(strength),
                        val screenSeconds: Int = 15, val sound:Boolean=false, val soundSource:String="system") {
    fun validate(now: Long) {
        require(id.matches(Regex("[A-Za-z0-9_-]{1,64}"))) { "invalid reminder id" }
        require(title.isNotBlank() && title.codePointCount(0,title.length) <= 60) { "title must be 1..60 characters" }
        require(body.isNotBlank() && body.codePointCount(0,body.length) <= 500) { "body must be 1..500 characters" }
        require((title + body).none { it.code < 32 && it != '\n' && it != '\t' }) { "unsupported control character" }
        require(!(title+body).codePoints().anyMatch{it in 0xD800..0xDFFF}) { "invalid Unicode text" }
        require(expiresAtMs > now && expiresAtMs - now <= 300_000) { "reminder expired or beyond five minutes" }
        require(screenSeconds in 5..30) { "screenSeconds must be 5..30" }
        ReminderSoundPolicy.validateSource(soundSource)
        vibration.validate()
    }
}

/** Source is explicit so a future local custom asset resolver does not change the wire shape. */
object ReminderSoundPolicy {
    const val SYSTEM="system"
    const val CUSTOM="custom"
    fun validateSource(source:String) {
        require(source==SYSTEM) { if(source==CUSTOM)"custom sound is not implemented" else "unknown sound source" }
    }
    fun suppression(requested:Boolean,modeAllowed:Boolean,volume:Int?,muted:Boolean?):String?=when {
        !requested -> "not_requested"
        !modeAllowed -> "system_mode_restricted"
        volume==null || muted==null -> "audio_state_unknown"
        muted || volume<=0 -> "notification_volume_muted"
        else -> null
    }
}

data class ReminderVibration(val amplitude: Int, val pulseMs: Long, val gapMs: Long, val count: Int = 2) {
    fun validate(){
        require(amplitude in 1..255 && pulseMs in 20..1000 && gapMs in 0..2000 && count in 1..8) { "invalid vibration parameters" }
        require(totalMs()<=10_000) { "vibration sequence exceeds ten seconds" }
    }
    fun totalMs() = pulseMs*count + gapMs*(count-1)
    fun timings() = LongArray(count*2) { if(it%2==1)pulseMs else if(it==0)0 else gapMs }
    fun amplitudes() = IntArray(count*2) { if(it%2==1)amplitude else 0 }
    companion object {
        fun comparisonTimings()=longArrayOf(0,120,500,120,2000,220,500,220,2000,360,500,360)
        fun comparisonAmplitudes()=intArrayOf(0,96,0,96,0,192,0,192,0,255,0,255)
        fun profile(strength: String) = when (strength) {
            "light" -> ReminderVibration(96, 120, 120)
            "standard" -> ReminderVibration(192, 220, 160)
            "strong" -> ReminderVibration(255, 360, 220)
            else -> throw IllegalArgumentException("strength must be light, standard or strong")
        }
    }
}
