package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class InteractionStateTest {
    @Test fun duplicateEnableDoesNotRenewTheDeadline() {
        val state=InteractionState()
        state.set(true,100,1000);state.set(true,900,1000)
        assertEquals(1100L,state.deadlineMs)
        assertFalse(state.expire(1099));assertTrue(state.expire(1100));assertFalse(state.enabled)
    }
    @Test fun stoppingCannotBeReversedByAnOldDeadline() {
        val state=InteractionState()
        state.set(true,0,1000);state.set(false,50,1000)
        assertNull(state.deadlineMs);assertFalse(state.expire(2000));assertFalse(state.enabled)
    }
    @Test fun savingDurationWhileClosedNeverEnablesBusiness() {
        val state=InteractionState();state.updateDuration(10,500)
        assertFalse(state.enabled);assertNull(state.deadlineMs)
        state.set(true,100,500);state.updateDuration(300,900)
        assertEquals(1200L,state.deadlineMs)
    }
    @Test fun unlimitedStillSupportsExplicitStopAndANewSession() {
        val state=InteractionState();state.set(true,100,0)
        assertFalse(state.expire(Long.MAX_VALUE));assertTrue(state.enabled)
        state.set(false,200,0);state.set(true,300,500)
        assertEquals(800L,state.deadlineMs)
    }
}
