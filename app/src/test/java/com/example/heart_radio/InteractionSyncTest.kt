package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class InteractionSyncTest {
    private var now=1000L
    private val pc=InteractionSync("pc"){now}
    private val watch=InteractionSync("watch"){now+900_000}
    private fun settle(){repeat(3){watch.merge(pc.snapshot());pc.merge(watch.snapshot())};assertEquals(pc.fingerprint(),watch.fingerprint());assertEquals(pc.enabled,watch.enabled)}
    @Test fun eitherEndCanChangeWithoutEcho(){
        pc.edit(true,60_000);settle();assertTrue(watch.enabled)
        watch.edit(false);settle();assertFalse(pc.enabled)
        watch.edit(true);settle();assertTrue(pc.enabled)
        pc.edit(false);settle();assertFalse(watch.enabled)
    }
    @Test fun offlineConflictClosesAndLaterUserCanReopen(){
        pc.edit(true);settle();pc.edit(false);pc.edit(true);watch.edit(false);settle();assertFalse(pc.enabled)
        pc.edit(true);settle();assertTrue(watch.enabled)
    }
    @Test fun concurrentOpensUseShortestLeaseAcrossDifferentClockEpochs(){
        pc.edit(true,10_000);watch.edit(true,5_000);settle();assertEquals(5_000L,pc.remaining())
        now+=5_001;watch.expire();settle();assertFalse(pc.enabled)
    }
    @Test fun duplicateAndReconnectNeverRenew(){
        pc.edit(true,10_000);val old=pc.snapshot();settle();val ends=watch.deadlines.toMap();now+=3_000
        watch.merge(old);watch.merge(old);pc.edit(true,50_000);settle()
        assertEquals(ends,watch.deadlines);assertEquals(7_000L,pc.remaining())
    }
    @Test fun expirySurvivesDelayedOpenPacket(){
        pc.edit(true,2_000);val old=pc.snapshot();settle();now+=2_001;pc.expire();settle();watch.merge(old);assertFalse(watch.enabled)
    }
    @Test fun explicitDurationEditPropagatesOnce(){
        watch.edit(true,10_000);settle();now+=1_000;pc.edit(true,3_000,true);settle();now+=1_000;settle();assertEquals(2_000L,watch.remaining())
    }
    @Test fun identityConflictRejectedWithoutChangingState(){
        pc.edit(true);settle();val old=watch.snapshot();val event=old.pc!!.event.copy(id="f".repeat(32))
        try{watch.merge(old.copy(pc=GateWire(event,null)));fail("expected conflict")}catch(_:IllegalArgumentException){}
        assertEquals(old,watch.snapshot())
    }
    @Test fun cycleAndInvalidLeaseAreRejected(){
        val p=GateEvent(1,1,"a".repeat(32),true,1000);val w=GateEvent(1,1,"b".repeat(32),false,null)
        for(s in listOf(GateSnapshot(GateWire(p,1000),GateWire(w,null)),GateSnapshot(GateWire(p.copy(seen=0),1001),null))){
            try{watch.merge(s);fail("expected rejection")}catch(_:IllegalArgumentException){}
        }
        assertFalse(watch.enabled)
    }
}
