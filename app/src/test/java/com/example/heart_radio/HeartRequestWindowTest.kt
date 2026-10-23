package com.example.heart_radio
import org.junit.Assert.*
import org.junit.Test

class HeartRequestWindowTest{
    @Test fun deadlineIsExactlyFifteenSeconds(){val w=HeartRequestWindow(100,15);assertFalse(w.expired(15_000_000_099));assertTrue(w.expired(15_000_000_100));assertEquals("timeout",w.timeoutStatus())}
    @Test fun noContactAndInvalidDataStayWithoutValue(){val w=HeartRequestWindow(100,15);w.reading.registered=true
        assertFalse(w.reading.accept(0f,-1,101,102,1));assertEquals("no_contact",w.timeoutStatus());assertNull(w.reading.recentValue(103))
        assertFalse(w.reading.accept(Float.NaN,3,103,104,2));assertEquals("no_valid_data",w.timeoutStatus())}
    @Test fun validNewReceiptDoesNotRewriteOldSourceTime(){val w=HeartRequestWindow(1_000_000_000_000,15);w.reading.registered=true
        assertTrue(w.reading.accept(73f,3,1000,1_000_000_001_000,1));assertEquals(1000,w.reading.lastEventNs);assertNotNull(w.reading.recentValue(1_000_000_001_100))}
    @Test(expected=IllegalArgumentException::class) fun aboveFifteenIsRejected(){HeartRequestWindow(100,16)}
    @Test fun registrationTimeConsumesTheSameWindow(){val w=HeartRequestWindow(100,15)
        assertEquals(15000L,w.remainingMs(100));assertEquals(14000L,w.remainingMs(1_000_000_100))
        assertEquals(1L,w.remainingMs(15_000_000_099));assertEquals(0L,w.remainingMs(15_000_000_100));assertEquals(0L,w.remainingMs(99))}
}
