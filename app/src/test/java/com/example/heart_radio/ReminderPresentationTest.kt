package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class ReminderPresentationTest {
    @Test fun ownerCanReadExpiredOrCancelledTextWhileInteractionIsOff(){
        assertTrue(ReminderPresentation.canOpen(true,false,false,true,false,10,20))
        assertTrue(ReminderPresentation.canOpen(true,false,false,false,true,10,20))
    }
    @Test fun lockStillProtectsSavedAndAutomaticContent(){
        for(history in listOf(false,true))assertFalse(ReminderPresentation.canOpen(history,true,true,false,false,30,20))
    }
    @Test fun delayedAutomaticLaunchCannotReviveExpiredCancelledOrAcknowledgedReminder(){
        assertTrue(ReminderPresentation.canOpen(false,false,true,false,false,30,20))
        assertFalse(ReminderPresentation.canOpen(false,false,true,false,false,20,20))
        assertFalse(ReminderPresentation.canOpen(false,false,true,true,false,30,20))
        assertFalse(ReminderPresentation.canOpen(false,false,true,false,true,30,20))
        assertFalse(ReminderPresentation.canOpen(false,false,false,false,false,30,20))
    }
    @Test fun existingTaskPresentationHonorsEffectPermissionAndStaysOnValidatedApi(){
        assertTrue(ReminderPresentation.useExistingTask(27,true,true,false))
        assertFalse(ReminderPresentation.useExistingTask(27,false,true,false))
        assertFalse(ReminderPresentation.useExistingTask(27,true,false,false))
        assertFalse(ReminderPresentation.useExistingTask(27,true,true,true))
        assertFalse(ReminderPresentation.useExistingTask(36,true,true,false))
    }
}
