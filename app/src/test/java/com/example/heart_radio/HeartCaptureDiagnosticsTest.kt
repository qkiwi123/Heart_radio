package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class HeartCaptureDiagnosticsTest {
    private val start=1_000_000_000L
    private fun ns(seconds:Long)=start+seconds*1_000_000_000L
    private fun begun():Pair<ReadingState,HeartCaptureDiagnostics> {
        val reading=ReadingState().apply{reset(start);registered=true}
        val diagnostic=HeartCaptureDiagnostics().apply{begin("attempt-a",start);registered()}
        return reading to diagnostic
    }
    private fun callback(r:ReadingState,d:HeartCaptureDiagnostics,seconds:Long,bpm:Float=75f,accuracy:Int=3,sourceNs:Long=ns(seconds)) {
        val valid=r.accept(bpm,accuracy,sourceNs,ns(seconds),100)
        d.callback(ns(seconds),valid)
    }
    @Test fun registrationAndIdleDoNotClaimData() {
        val r=ReadingState();val d=HeartCaptureDiagnostics()
        assertEquals("idle",d.snapshot(r,start).state)
        assertNull(d.snapshot(r,start).attemptId)
        d.begin("a",start);r.reset(start)
        assertEquals("registering",d.snapshot(r,start).state)
        r.registered=true;d.registered()
        assertEquals("waiting_first_data",d.snapshot(r,ns(1)).state)
        assertNull(d.snapshot(r,ns(1)).firstCallbackAfterMs)
    }
    @Test fun historicalTwentySecondSilenceIsWaitingAndNinetySecondBoundaryIsExplicit() {
        val (r,d)=begun()
        assertEquals("waiting_first_data",d.snapshot(r,ns(20)+681_000_000L).state)
        assertEquals("waiting_first_data",d.snapshot(r,ns(90)-1).state)
        assertFalse(d.snapshot(r,ns(90)-1).firstCallbackTimedOut)
        assertEquals("first_data_timeout",d.snapshot(r,ns(90)).state)
        assertTrue(d.snapshot(r,ns(90)).firstCallbackTimedOut)
        assertTrue(r.running);assertTrue(r.registered);assertEquals(0,r.callbacks)
    }
    @Test fun lateDataCanRecoverWithoutNewAttemptAndKeepsLateFirstCallbackEvidence() {
        val (r,d)=begun()
        assertEquals("first_data_timeout",d.snapshot(r,ns(90)).state)
        callback(r,d,91)
        val view=d.snapshot(r,ns(92))
        assertEquals("ready",view.state);assertEquals("attempt-a",view.attemptId)
        assertEquals(91_000L,view.firstCallbackAfterMs);assertEquals(91_000L,view.firstValidAfterMs)
        assertTrue(view.firstCallbackTimedOut)
    }
    @Test fun noContactAndInvalidDataAreDifferentFromWaitingAndValidValues() {
        val (r,d)=begun()
        callback(r,d,5,0f,-1)
        assertEquals("no_contact",d.snapshot(r,ns(6)).state)
        assertEquals(5_000L,d.snapshot(r,ns(6)).firstCallbackAfterMs)
        assertNull(d.snapshot(r,ns(6)).firstValidAfterMs)
        r.accuracyChanged(3)
        assertEquals("invalid_data",d.snapshot(r,ns(6)).state)
        callback(r,d,10)
        assertEquals("ready",d.snapshot(r,ns(10)).state)
        assertEquals(10_000L,d.snapshot(r,ns(10)).firstValidAfterMs)
    }
    @Test fun receiptRecencyDoesNotRewriteOldSourceTimestamp() {
        val (r,d)=begun()
        callback(r,d,40,sourceNs=ns(1))
        assertEquals(ns(1),r.lastEventNs)
        assertEquals("ready",d.snapshot(r,ns(70)).state)
        assertEquals(30_000L,d.snapshot(r,ns(70)).lastCallbackAgeMs)
        assertEquals("stale",d.snapshot(r,ns(70)+1_000_000L).state)
        assertNull(r.recentValue(ns(70)+1_000_000L))
    }
    @Test fun stoppingFreezesAttemptWaitAndNewAttemptCannotReuseOldMeasurements() {
        val (r,d)=begun()
        r.stop();d.stopped("link_closed",ns(20))
        val stopped=d.snapshot(r,ns(100))
        assertEquals("stopped",stopped.state);assertEquals("link_closed",stopped.stopReason)
        assertEquals(20_000L,stopped.elapsedMs);assertFalse(stopped.firstCallbackTimedOut)
        r.reset(ns(100));r.registered=true;d.begin("attempt-b",ns(100));d.registered()
        assertEquals("waiting_first_data",d.snapshot(r,ns(101)).state)
        assertNull(d.snapshot(r,ns(101)).stopReason)
        assertNull(d.snapshot(r,ns(101)).firstCallbackAfterMs)
    }
    @Test fun failedRegistrationNeverTurnsIntoFirstDataTimeout() {
        val r=ReadingState().apply{reset(start);stop()}
        val d=HeartCaptureDiagnostics().apply{begin("failed",start);failed("permission_denied",ns(1))}
        val view=d.snapshot(r,ns(200))
        assertEquals("registration_failed",view.state);assertEquals("permission_denied",view.failureReason)
        assertFalse(view.firstCallbackTimedOut)
    }
    @Test fun futureReceiptDoesNotShowAReadyValue() {
        val (r,d)=begun();callback(r,d,10)
        assertEquals("clock_anomaly",d.snapshot(r,ns(9)).state)
    }
}
