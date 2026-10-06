package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class BridgeLifecycleTest {
    @Test fun restartDoesNotRenewDeadline() {
        val lease = BridgeLease.start(1_000, 10_000, 7)
        assertEquals(BridgeLease.DEFAULT_MS - 60_000, lease.remaining(61_000, 70_000, 7))
        assertEquals(0, lease.remaining(1_801_000, 1_810_000, 7))
    }
    @Test fun rebootOrUnknownBootCannotRestoreLease() {
        val lease = BridgeLease.start(1_000, 10_000, 7)
        assertEquals(0, lease.remaining(1_100, 10_100, 8))
        assertEquals(0, lease.remaining(500, 10_100, 7))
        assertEquals(0, lease.remaining(1_100, 10_100, -1))
    }
    @Test fun changingWallClockCannotExtendMonotonicDeadline() {
        val lease = BridgeLease.start(1_000, 10_000, 7)
        assertEquals(BridgeLease.DEFAULT_MS - 100, lease.remaining(1_100, 0, 7))
        assertEquals(0, lease.remaining(1_100, 1_810_000, 7))
    }
    @Test fun repeatedShortConnectionsBackOffWithCeiling() {
        val policy = BridgeRetryPolicy()
        assertEquals(listOf(2000L, 4000L, 8000L, 16000L, 30000L, 30000L),
            (1..6).map { policy.disconnected(100) })
    }
    @Test fun stableConnectionRestartsSmallDelay() {
        val policy = BridgeRetryPolicy()
        repeat(5) { policy.disconnected(0) }
        assertEquals(2000L, policy.disconnected(30_000))
        assertEquals(4000L, policy.disconnected(1_000))
    }
    @Test fun customLeaseCanExceedOldThirtyMinuteLimitAndExpiresWithoutRenewal() {
        val lease = BridgeLease.start(1_000,10_000,7,3_600_000)
        assertEquals(3_540_000,lease.remaining(61_000,70_000,7))
        assertEquals(0,lease.remaining(3_601_000,3_610_000,7))
    }
    @Test fun unlimitedStillCannotRestoreOnAnotherBootOrAfterClockReset() {
        val lease = BridgeLease.start(1_000,10_000,7,0)
        assertEquals(Long.MAX_VALUE,lease.remaining(99_000_000,99_000_000,7))
        assertEquals(0,lease.remaining(2_000,20_000,8))
        assertEquals(0,lease.remaining(500,20_000,7))
    }
    @Test fun durationUnitsAndBounds() {
        assertEquals(5_400_000,StandbyDuration.parse(false,1.5,true))
        assertEquals(6_000,StandbyDuration.parse(false,.1,false))
        assertEquals(0,StandbyDuration.parse(true,Double.NaN,false))
        for (value in listOf(-1.0,0.0,.0001,Double.NaN,Double.POSITIVE_INFINITY,999_999.0)) {
            try { StandbyDuration.parse(false,value,true);fail("Expected invalid duration") } catch (_: IllegalArgumentException) { }
        }
    }
    @Test fun syncUsesComputerRemainingInsteadOfFullDuration() {
        val lease = StandbySync.lease(3600.0,25_000,1000,10_000,7)
        assertEquals(25_000,lease.remaining(1000,10_000,7))
        assertEquals(3_600_000,lease.durationMs)
    }
    @Test fun reconnectAndDelayedDuplicateCannotExtendSameRevision() {
        val previous = StandbySync.lease(60.0,30_000,1000,10_000,7)
        val renewed = StandbySync.lease(60.0,29_000,5000,14_000,7,previous,true)
        assertEquals(26_000,renewed.remaining(5000,14_000,7))
        val saved = StandbySync.lease(60.0,59_000,5000,14_000,7,previous,false)
        assertEquals(59_000,saved.remaining(5000,14_000,7))
    }
    @Test fun syncedUnlimitedAndExpiredPeriodsKeepStopSemantics() {
        assertEquals(Long.MAX_VALUE,StandbySync.lease(0.0,null,1000,10_000,7).remaining(5000,14_000,7))
        assertEquals(0,StandbySync.lease(1.0,0,1000,10_000,7).remaining(1000,10_000,7))
        assertEquals(0,StandbySync.lease(0.0,null,1000,10_000,7).remaining(5000,14_000,8))
    }
    @Test fun syncRejectsInvalidOrInconsistentLimits() {
        for ((seconds,left) in listOf(Double.NaN to null,0.0 to 1L,1.0 to null,1.0 to 1001L,1.0 to -1L,.5 to 500L)) {
            try { StandbySync.lease(seconds,left,1000,10_000,7);fail("Expected invalid sync") } catch (_: IllegalArgumentException) {}
        }
    }
}
