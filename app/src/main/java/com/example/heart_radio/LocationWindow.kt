package com.example.heart_radio

/** Experimental first-fix budget; independent of the daily motion/pressure tool limits. */
object LocationWindow {
    const val MAX_SECONDS = 180
    fun isFresh(fixNs: Long, beginNs: Long, receivedNs: Long, deadlineNs: Long) =
        fixNs >= beginNs && fixNs <= receivedNs && receivedNs < deadlineNs
}
