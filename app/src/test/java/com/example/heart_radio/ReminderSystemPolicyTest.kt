package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class ReminderSystemPolicyTest {
    private fun watch(filter:Int=4,ringer:Int?=2,silent:Int?=1,dnd:Int?=0)=
        ReminderSystemPolicy.decide(ReminderSystemPolicy.Input(filter,ringer,true,silent,dnd))
    @Test fun normalModeAllowsEffectsAndFutureSound(){
        val value=watch(1,2,0,0);assertTrue(value.effectsAllowed);assertTrue(value.soundAllowedByMode)
        assertFalse(value.silentCompatibility);assertEquals(5,value.vibrationAudioUsage)
    }
    @Test fun verifiedWatchSilentFilterAllowsOnlyNonAudioEffects(){
        for(ringer in listOf(0,2)) {
            val value=watch(ringer=ringer);assertEquals("silent",value.mode)
            assertTrue(value.effectsAllowed);assertTrue(value.silentCompatibility)
            assertFalse(value.suppressedByDnd);assertFalse(value.soundAllowedByMode);assertEquals(4,value.vibrationAudioUsage)
        }
    }
    @Test fun silentWithUnfilteredOsDoesNotNeedAlarmCompatibility(){
        val value=watch(filter=1);assertEquals("silent",value.mode);assertTrue(value.effectsAllowed)
        assertFalse(value.silentCompatibility);assertEquals(5,value.vibrationAudioUsage);assertFalse(value.soundAllowedByMode)
    }
    @Test fun realDndWinsOverSilentAndEvenContradictoryFilter(){
        for(filter in listOf(0,1,2,3,4)) {
            val value=watch(filter=filter,dnd=1)
            assertEquals("do_not_disturb",value.mode);assertFalse(value.effectsAllowed)
            assertTrue(value.suppressedByDnd);assertFalse(value.silentCompatibility);assertFalse(value.soundAllowedByMode)
        }
    }
    @Test fun unknownVendorFlagsNeverEnableCompatibility(){
        for(value in listOf(watch(dnd=null),watch(silent=null),watch(silent=0))) {
            assertFalse(value.effectsAllowed);assertFalse(value.silentCompatibility)
            assertEquals("system_restricted",value.mode);assertFalse(value.suppressedByDnd)
        }
    }
    @Test fun strongerOrUnknownFilterNeverUsesSilentCompatibility(){
        for(filter in listOf(0,2,3,9)) {
            val value=watch(filter=filter);assertFalse(value.effectsAllowed);assertFalse(value.silentCompatibility)
        }
    }
    @Test fun anotherModelCannotBorrowVendorSilentException(){
        val value=ReminderSystemPolicy.decide(ReminderSystemPolicy.Input(4,2,false,1,0))
        assertFalse(value.effectsAllowed);assertTrue(value.suppressedByDnd);assertFalse(value.silentCompatibility)
    }
    @Test fun standardSilentAndVibrateModesRecordNoFutureSound(){
        for(ringer in listOf(0,1)) {
            val value=ReminderSystemPolicy.decide(ReminderSystemPolicy.Input(1,ringer))
            assertTrue(value.effectsAllowed);assertFalse(value.soundAllowedByMode);assertFalse(value.suppressedByDnd)
        }
    }
    @Test fun unknownRingerDoesNotAssumeFutureSoundPermission(){
        val value=watch(1,null,0,0);assertTrue(value.effectsAllowed);assertFalse(value.soundAllowedByMode)
    }
    @Test fun aKnownSilentSwitchNeverAllowsFutureSoundWhenDndFlagIsUnavailable(){
        val value=watch(1,2,1,null);assertTrue(value.effectsAllowed)
        assertFalse(value.soundAllowedByMode);assertFalse(value.silentCompatibility)
    }
}
