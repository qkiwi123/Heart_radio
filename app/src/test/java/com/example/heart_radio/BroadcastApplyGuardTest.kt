package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class BroadcastApplyGuardTest {
    @Test fun explicitResumeRestartsTheSameSavedOnVersionExactlyOnce(){
        val guard=BroadcastApplyGuard()
        assertEquals(true,guard.next("saved-on",true,false))
        assertNull(guard.next("saved-on",true,false))
        guard.invalidate()
        assertEquals(true,guard.next("saved-on",true,false))
        assertNull(guard.next("saved-on",true,false))
        guard.invalidate();assertNull(guard.next("saved-off",false,false))
    }
    @Test fun pendingOrFailedStartIsNotRepeatedByPollingOrReconnect(){
        val guard=BroadcastApplyGuard()
        assertEquals(true,guard.next("user-on-1",true,false))
        assertNull(guard.next("user-on-1",true,false))
        assertNull(guard.next("user-on-1",true,true))
        assertEquals(true,guard.next("user-on-2",true,false))
    }
    @Test fun turningOffDoesNotAffectAnotherLeaseAndAlreadyRunningIsNotRestarted(){
        val guard=BroadcastApplyGuard()
        assertNull(guard.next("on",true,true))
        assertEquals(false,guard.next("off",false,true))
        assertNull(guard.next("off",false,false))
    }
}
