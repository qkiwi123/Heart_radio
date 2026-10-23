package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class OfflineTimeoutTest {
    @Test fun initialFailureStopsAtTenMinutesOnce(){
        val timer=OfflineTimeout();timer.start(100)
        assertFalse(timer.expired(600099,false));assertTrue(timer.expired(600100,false))
        assertFalse(timer.expired(700100,false))
    }
    @Test fun eitherUsableTransportPreventsShutdownAndLossStartsANewWindow(){
        val timer=OfflineTimeout();timer.start(0)
        assertFalse(timer.expired(599999,false));assertFalse(timer.expired(900000,true))
        assertFalse(timer.expired(900010,false));assertFalse(timer.expired(1500009,false))
        assertTrue(timer.expired(1500010,false))
    }
    @Test fun aManualNewSessionRestoresItsEntireBudget(){
        val timer=OfflineTimeout();timer.start(0);assertTrue(timer.expired(600000,false))
        timer.start(600100);assertFalse(timer.expired(600101,false))
        assertEquals(599999L,timer.remaining(600101,false));assertTrue(timer.expired(1200100,false))
    }
}
