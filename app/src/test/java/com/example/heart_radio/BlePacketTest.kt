package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class BlePacketTest {
    @Test fun crossLanguageGoldenPacket() {
        val bytes = BlePacket.encode(1, 0x01020304, 65535, 1700000000000L, 72.5f, 3, true, true)
        assertEquals("010104030201ffff0068e5cf8b010000d5020307", bytes.joinToString("") { "%02x".format(it.toInt() and 255) })
    }
    @Test fun noReadingHasNoValidFlag() {
        val bytes = BlePacket.encode(1, 1, 0, 1700000000000L, null, -1, true, true)
        assertEquals(20, bytes.size)
        assertEquals(6, bytes[19].toInt())
        assertEquals(-1, bytes[18].toInt())
        assertEquals(0, bytes[16].toInt())
    }
}
