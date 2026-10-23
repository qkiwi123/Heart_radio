package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class MeasurementSlotsTest{
    @Test fun fourIndependentSensorsRunTogetherAndFinishingOneKeepsTheOthers(){
        val slots=MeasurementSlots()
        for(type in listOf(21,6,1,4))assertTrue(slots.reserve("id$type","sensor:$type"))
        assertFalse(slots.reserve("extra","sensor:9"))
        slots.release("id6")
        assertEquals(setOf("id21","id1","id4"),slots.entries().keys)
        assertTrue(slots.reserve("pressure2","sensor:6"))
    }
    @Test fun duplicateResourceOrIdCannotStartAnotherListener(){
        val slots=MeasurementSlots();assertTrue(slots.reserve("one","sensor:21"))
        assertFalse(slots.reserve("two","sensor:21"));assertFalse(slots.reserve("one","sensor:6"))
        assertEquals(mapOf("one" to "sensor:21"),slots.entries())
    }
    @Test fun locationRemainsExclusiveInBothDirections(){
        val slots=MeasurementSlots();assertTrue(slots.reserve("heart","sensor:21"))
        assertFalse(slots.reserve("gps","location"));slots.release("heart")
        assertTrue(slots.reserve("gps","location"));assertFalse(slots.reserve("heart2","sensor:21"))
        slots.release("gps");assertTrue(slots.reserve("heart2","sensor:21"))
    }
}
