package com.example.heart_radio

class HeartRadioApplication : android.app.Application() {
    override fun onCreate(){super.onCreate();ProjectSession.initialize(this);ReminderEngine.initialize(this);ReminderPolicy.initialize(this);AgentInteraction.initialize(this);HeartBroadcast.initialize(this)}
}
