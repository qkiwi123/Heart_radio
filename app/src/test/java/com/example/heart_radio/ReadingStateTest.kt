package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class ReadingStateTest {
    private fun running() = ReadingState().apply { reset(1_000_000_000L); registered = true }
    @Test fun permissionAndNoSensorTakePriority() {
        val s = running()
        assertEquals("无身体传感器权限", s.status(2_000_000_000L, false, true))
        assertEquals("未找到标准心率传感器", s.status(2_000_000_000L, true, false))
    }
    @Test fun noCallbacksBecomesStale() {
        val s = running()
        assertEquals("等待心率回调", s.status(2_000_000_000L, true, true))
        assertEquals("30 秒仍无回调", s.status(32_000_000_001L, true, true))
    }
    @Test fun invalidContactZeroAndNanNeverDisplay() {
        for ((i, pair) in listOf(0f to 3, Float.NaN to 3, 70f to 0, 70f to -1, -5f to 2).withIndex()) {
            val s = running(); val time = 2_000_000_000L + i
            assertFalse(s.accept(pair.first, pair.second, time, time, 100))
            assertNull(s.recentValue(time)); assertEquals(0, s.validCount); assertEquals(1, s.callbacks)
        }
    }
    @Test fun sourceTimestampAgeDoesNotHideRecentlyReceivedValue() {
        val s = running()
        assertTrue(s.accept(75f, 3, 2_000_000_000L, 40_000_000_000L, 100))
        assertEquals(1, s.validCount); assertEquals(75f, s.recentValue(40_000_000_000L)!!, 0f)
        assertEquals(2_000_000_000L, s.lastEventNs)
        assertEquals("源时间戳落后，未校正", s.sourceTimeNote())
        assertNotNull(s.recentValue(70_000_000_000L))
        assertNull(s.recentValue(70_000_000_001L))
        assertNull(s.recentValue(39_000_000_000L))
        assertEquals("已收到有效数值", s.status(40_000_000_000L, true, true))
        assertEquals("30 秒无新回调", s.status(71_000_000_001L, true, true))
        assertTrue(s.accept(76f, 3, 41_000_000_000L, 41_000_000_000L, 101))
        assertNotNull(s.recentValue(41_000_000_000L)); s.stop(); assertNull(s.recentValue(41_000_000_000L))
        assertFalse(s.accept(77f, 3, 42_000_000_000L, 42_000_000_000L, 102)); assertEquals(2, s.callbacks)
        s.reset(43_000_000_000L); s.registered = true
        assertNull(s.recentValue(43_000_000_000L)); assertEquals(0, s.callbacks)
    }
    @Test fun freshnessBoundaryAndAccuracyChange() {
        val s = running(); s.accept(70f, 3, 2_000_000_000L, 2_000_000_000L, 0)
        assertNotNull(s.recentValue(32_000_000_000L)); assertNull(s.recentValue(32_000_000_001L))
        s.accuracyChanged(0); assertNull(s.recentValue(3_000_000_000L))
    }
    @Test fun invalidReadingAndAccuracyRecoveryCannotResurrectPreviousValue() {
        val s = running(); s.accept(70f, 3, 2_000_000_000L, 2_000_000_000L, 0)
        s.accuracyChanged(-1)
        assertEquals("无接触，请佩戴", s.status(3_000_000_000L, true, true))
        assertNull(s.recentValue(3_000_000_000L))
        s.accuracyChanged(3); assertNull(s.recentValue(3_000_000_000L))
        s.accept(71f, 3, 4_000_000_000L, 4_000_000_000L, 1)
        assertNotNull(s.recentValue(4_000_000_000L))
        s.accept(0f, -1, 5_000_000_000L, 5_000_000_000L, 2)
        assertNull(s.recentValue(5_000_000_000L))
        assertEquals(4_000_000_000L, s.lastValidDeliveryNs)
    }
    @Test fun failedRegistrationIsNotWaitingForData() {
        val s = running(); s.registered = false; s.result = "失败：registerListener 返回 false"
        assertEquals(s.result, s.status(2_000_000_000L, true, true))
        assertFalse(s.accept(75f, 3, 2_000_000_000L, 2_000_000_000L, 0))
    }
    @Test fun syntheticRegressionReplayPreservesValuesAndRawTimestamps() {
        // Synthetic values and timestamps; no personal recording is bundled.
        val s = running(); var visible = 0; var oldPolicyVisible = 0; var lastArrival = 0L
        val stream = requireNotNull(javaClass.getResourceAsStream("/synthetic-session.csv"))
        stream.bufferedReader().useLines { lines ->
            lines.filter { it.isNotBlank() && !it.startsWith("#") }.forEach { line ->
                val c = line.split(','); val eventNs = c[2].toLong(); val arrival = c[3].toLong()
                val valid = s.accept(c[0].toFloat(), c[1].toInt(), eventNs, arrival, c[4].toLong())
                assertEquals(eventNs, s.lastEventNs)
                if (valid && arrival - eventNs <= ReadingState.STALE_NS) oldPolicyVisible++
                if (s.recentValue(arrival) != null) visible++
                lastArrival = arrival
            }
        }
        assertEquals(442, s.callbacks); assertEquals(441, s.validCount)
        assertEquals(11, oldPolicyVisible); assertEquals(441, visible)
        assertNull(s.recentValue(lastArrival)) // Synthetic final event models no contact.
        assertEquals("无接触，请佩戴", s.status(lastArrival, true, true))
    }
    @Test fun duplicateFutureAndRegressingTimestampsAreInvalid() {
        val s = running(); s.accept(70f, 3, 2_000_000_000L, 2_000_000_000L, 0)
        assertFalse(s.accept(71f, 3, 2_000_000_000L, 3_000_000_000L, 0))
        assertFalse(s.accept(71f, 3, 1_000_000_000L, 3_000_000_000L, 0))
        assertFalse(s.accept(71f, 3, 4_000_000_000L, 3_000_000_000L, 0))
    }
}
