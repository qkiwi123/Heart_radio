package com.example.heart_radio
import org.junit.Assert.*
import org.junit.Test

class ReminderPolicyRegisterTest{
    @Test fun defaultIsSixSeconds(){assertEquals(6000,ReminderPolicyRegister("pc").effective().maxVibrationMs)}
    @Test fun concurrentEditsChooseStricterAndLaterHumanEditCanRaise(){
        val p=ReminderPolicyRegister("pc");val w=ReminderPolicyRegister("watch")
        p.edit(ReminderLimits(true,9000,true,30));w.edit(ReminderLimits(false,3000,false,10))
        p.merge(w.snapshot());w.merge(p.snapshot());assertEquals(p.effective(),w.effective());assertFalse(p.effective().vibrationEnabled);assertEquals(3000,p.effective().maxVibrationMs)
        p.edit(ReminderLimits(true,8000,true,20));w.merge(p.snapshot());assertEquals(p.effective(),w.effective());assertEquals(8000,w.effective().maxVibrationMs)
    }
    @Test fun duplicateMergeDoesNotCreateAnEdit(){val p=ReminderPolicyRegister("pc");p.edit(ReminderLimits());val w=ReminderPolicyRegister("watch");w.merge(p.snapshot());val version=w.fingerprint();w.merge(p.snapshot());assertEquals(version,w.fingerprint())}
    @Test fun sixSecondLimitIncludesGaps(){val p=ReminderLimits();assertNull(p.violation(true,6000,false,15));assertEquals("vibration_exceeds_user_limit",p.violation(true,6001,false,15))}
    @Test fun userFlagsAndScreenCeilingAreEnforced(){val p=ReminderLimits(false,6000,false,10);assertEquals("vibration_disabled_by_user",p.violation(true,1000,false,10));assertEquals("wake_disabled_by_user",p.violation(false,0,true,5));assertEquals("screen_exceeds_user_limit",p.copy(wakeEnabled=true).violation(false,0,true,11))}
    @Test(expected=IllegalArgumentException::class) fun missingHistoryRejected(){ReminderPolicyRegister("pc").merge(PolicySnapshot(PolicyEvent(1,1,"a".repeat(32),ReminderLimits()),null))}
    @Test(expected=IllegalArgumentException::class) fun conflictingIdentityRejected(){val p=ReminderPolicyRegister("pc");p.edit(ReminderLimits());p.merge(PolicySnapshot(p.pc!!.copy(value=ReminderLimits(maxVibrationMs=3000)),null))}
    @Test(expected=IllegalArgumentException::class) fun invalidLimitRejected(){ReminderPolicyRegister("pc").edit(ReminderLimits(maxVibrationMs=10001))}
}
