package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class ReminderSpecTest {
    private fun spec(id: String = "test_1", body: String = "这是测试提醒。\n点击知道了。", expires: Long = 101) =
        ReminderSpec(id, "蓝牙测试", body, true, true, expires)
    @Test fun acceptsChineseAndLineBreaks() { spec().validate(100) }
    @Test fun rejectsExpiredAndFutureBacklog() {
        for (expiry in listOf(100L, 99L, 300101L)) {
            try { spec(expires = expiry).validate(100); fail("expiry $expiry") } catch (_: IllegalArgumentException) { }
        }
    }
    @Test fun rejectsLongOrControlTextAndInvalidIdentity() {
        for (row in listOf(spec(body = "字".repeat(501)), spec(body = "a\u0000b"), spec(id = "../old"))) {
            try { row.validate(100); fail("invalid payload accepted") } catch (_: IllegalArgumentException) { }
        }
    }
    @Test fun strengthProfilesAreBoundedAndOrdered() {
        val light=ReminderVibration.profile("light")
        val standard=ReminderVibration.profile("standard")
        val strong=ReminderVibration.profile("strong")
        assertTrue(light.amplitude < standard.amplitude && standard.amplitude < strong.amplitude)
        for (profile in listOf(light,standard,strong)) {
            assertTrue(profile.amplitude in 1..255)
            assertTrue(profile.timings().sum() <= 1500)
            assertEquals(4,profile.amplitudes().size)
        }
        try { ReminderVibration.profile("unlimited"); fail("unbounded profile") } catch (_:IllegalArgumentException) { }
    }
    @Test fun comparisonHasExactlyTwoPulsesPerLevelAndRequestedGaps() {
        val timings=ReminderVibration.comparisonTimings()
        val amplitudes=ReminderVibration.comparisonAmplitudes()
        assertEquals(6900L,timings.sum())
        assertEquals(listOf(96,96,192,192,255,255),amplitudes.filter{it>0})
        assertEquals(listOf(500L,500L,500L),listOf(timings[2],timings[6],timings[10]))
        assertEquals(2000L,timings[4]);assertEquals(2000L,timings[8])
    }
    @Test fun customPulsesHaveExactlyRequestedCountDurationAndGaps(){
        val pattern=ReminderVibration(255,300,500,3);pattern.validate()
        assertArrayEquals(longArrayOf(0,300,500,300,500,300),pattern.timings())
        assertArrayEquals(intArrayOf(0,255,0,255,0,255),pattern.amplitudes());assertEquals(1900L,pattern.totalMs())
    }
    @Test fun rejectsUnboundedVibrationAndScreenDuration(){
        for(pattern in listOf(ReminderVibration(0,300,500,2),ReminderVibration(256,300,500,2),ReminderVibration(255,1001,500,2),
            ReminderVibration(255,300,500,0),ReminderVibration(255,1000,2000,8),ReminderVibration(255,300,-1,2))){
            try{pattern.validate();fail("invalid waveform")}catch(_:IllegalArgumentException){}
        }
        try{spec().copy(screenSeconds=31).validate(100);fail("unbounded screen")}catch(_:IllegalArgumentException){}
    }
    @Test fun countsWholeEmojiCharactersAndUsesStrongDefault(){
        val row=spec().copy(title="😀".repeat(60));row.validate(100)
        assertEquals(255,row.vibration.amplitude)
    }
}
