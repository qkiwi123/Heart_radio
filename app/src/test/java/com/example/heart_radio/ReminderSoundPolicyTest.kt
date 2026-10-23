package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class ReminderSoundPolicyTest {
    @Test fun oldRequestsAndPermissionsRemainSilent() {
        assertFalse(ReminderSpec("old","t","b",false,false,1000).sound)
        assertFalse(ReminderLimits().soundEnabled)
        assertEquals("sound_disabled_by_user",ReminderLimits().violation(false,0,false,15,true))
        assertNull(ReminderLimits(soundEnabled=true).violation(false,0,false,15,true))
    }
    @Test fun silentDndAndUnknownModesNeverRequestAudio() {
        for(mode in listOf(
            ReminderSystemPolicy.Input(1,0),ReminderSystemPolicy.Input(1,1),
            ReminderSystemPolicy.Input(4,2,true,1,0),ReminderSystemPolicy.Input(4,2,true,1,1),
            ReminderSystemPolicy.Input(0,null))) {
            val decision=ReminderSystemPolicy.decide(mode)
            assertEquals("system_mode_restricted",ReminderSoundPolicy.suppression(true,decision.soundAllowedByMode,9,false))
        }
        assertNull(ReminderSoundPolicy.suppression(true,ReminderSystemPolicy.decide(ReminderSystemPolicy.Input(1,2)).soundAllowedByMode,9,false))
    }
    @Test fun zeroVolumeMutedAndUnreadableVolumeDoNotIssueSound() {
        assertEquals("notification_volume_muted",ReminderSoundPolicy.suppression(true,true,0,false))
        assertEquals("notification_volume_muted",ReminderSoundPolicy.suppression(true,true,9,true))
        assertEquals("audio_state_unknown",ReminderSoundPolicy.suppression(true,true,null,false))
        assertEquals("audio_state_unknown",ReminderSoundPolicy.suppression(true,true,9,null))
        assertEquals("not_requested",ReminderSoundPolicy.suppression(false,true,9,false))
    }
    @Test fun concurrentHumanDisablingSoundWinsAndExplicitLaterEditCanEnableIt() {
        val pc=ReminderPolicyRegister("pc");val watch=ReminderPolicyRegister("watch")
        pc.edit(ReminderLimits(soundEnabled=true));watch.edit(ReminderLimits(soundEnabled=false))
        pc.merge(watch.snapshot());watch.merge(pc.snapshot())
        assertFalse(pc.effective().soundEnabled);assertEquals(pc.effective(),watch.effective())
        watch.edit(watch.effective().copy(soundEnabled=true));pc.merge(watch.snapshot())
        assertTrue(pc.effective().soundEnabled)
    }
    @Test(expected=IllegalArgumentException::class) fun reservedCustomSourceCannotPlayOrFallBack() {
        ReminderSoundPolicy.validateSource(ReminderSoundPolicy.CUSTOM)
    }
    @Test(expected=IllegalArgumentException::class) fun remoteUrisAreNotSoundSources() {
        ReminderSoundPolicy.validateSource("https://example.invalid/audio.wav")
    }
}
