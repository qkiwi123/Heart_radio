package com.example.heart_radio
import org.junit.Assert.*
import org.junit.Test

class MotionWindowTest {
    @Test fun gravityIsNotActivityAndAllThirtySecondsAreKept(){
        val w=MotionWindow(1,1_000_000L)
        repeat(780){w.accept(floatArrayOf(0f,0f,9.8f),1_000_000L+it*38_000_000L)}
        assertEquals(780,w.count());assertEquals(30,w.bins().size);assertEquals(0.0,w.rms(),1e-8)
        assertFalse(w.truncated);assertTrue(w.spanMs()>29000)
    }
    @Test fun rotationUsesVectorMagnitudeAndAccelerationUsesMedianDeviation(){
        val g=MotionWindow(4,0);g.accept(floatArrayOf(3f,4f,0f),1)
        assertEquals(5.0,g.rms(),1e-8)
        val a=MotionWindow(1,0);a.accept(floatArrayOf(0f,0f,9f),1);a.accept(floatArrayOf(0f,0f,11f),2)
        assertEquals(1.0,a.rms(),1e-8)
    }
    @Test fun invalidValuesAndHardCapAreExplicit(){
        val w=MotionWindow(1,0,30,2)
        w.accept(floatArrayOf(Float.NaN,0f,0f),1);assertEquals(1,w.invalidEvents)
        assertTrue(w.accept(floatArrayOf(0f,0f,10f),2));assertTrue(w.accept(floatArrayOf(0f,0f,10f),3))
        assertFalse(w.accept(floatArrayOf(0f,0f,10f),4));assertTrue(w.truncated);assertEquals(2,w.count())
        w.accept(floatArrayOf(0f,0f,10f),30_000_000_000L);assertEquals(2,w.count())
    }
}
