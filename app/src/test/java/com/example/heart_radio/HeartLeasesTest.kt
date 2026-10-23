package com.example.heart_radio
import org.junit.Assert.*
import org.junit.Test

class HeartLeasesTest{
    @Test fun sharesOneRegistrationAndOnlyLastReleaseStops(){
        var starts=0;var stops=0;val leases=HeartLeases<Any>({starts++;true},{stops++})
        assertTrue(leases.acquire("continuous",Any()));assertTrue(leases.acquire("once",Any()));assertEquals(1,starts)
        leases.release("once");assertEquals(1,leases.size);assertEquals(0,stops)
        leases.release("continuous");assertEquals(1,stops);assertEquals(0,leases.size)
    }
    @Test fun stoppingBroadcastDoesNotStopPendingRequest(){
        var stops=0;val leases=HeartLeases<Any>({true},{stops++})
        leases.acquire("once",Any());leases.acquire("continuous",Any());leases.release("continuous")
        assertEquals(1,leases.size);assertEquals(0,stops);leases.release("once");assertEquals(1,stops)
    }
    @Test fun failedRegistrationOwnsNothing(){val leases=HeartLeases<Any>({false},{fail("nothing to release")});assertFalse(leases.acquire("once",Any()));assertEquals(0,leases.size);leases.release("once")}
    @Test fun duplicateOwnerDoesNotRegisterAgain(){var starts=0;val item=Any();val leases=HeartLeases<Any>({starts++;true},{})
        assertTrue(leases.acquire("one",item));assertTrue(leases.acquire("one",item));assertFalse(leases.acquire("one",Any()));assertEquals(1,starts)}
    @Test fun reentrantReleaseSkipsRemovedSubscriber(){val leases=HeartLeases<Any>({true},{})
        val first=Any();leases.acquire("first",first);leases.acquire("second",Any());var calls=0
        leases.each{calls++;if(it===first)leases.release("second")};assertEquals(1,calls)}
}
