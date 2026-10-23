package com.example.heart_radio

/** Delivery deadlines stop automatic effects, not the owner's access to saved text. */
object ReminderPresentation {
    fun canOpen(history:Boolean, locked:Boolean, enabled:Boolean, cancelled:Boolean,
                acknowledged:Boolean, expiresAtMs:Long, now:Long):Boolean =
        !locked && (history || (enabled && !cancelled && !acknowledged && expiresAtMs>now))

    fun useExistingTask(sdk:Int, screenRequested:Boolean, permitted:Boolean, locked:Boolean):Boolean =
        sdk==27 && screenRequested && permitted && !locked
}
