package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class WifiConfigTest {
    private val suffix = "|" + "ab".repeat(32) + "|" + "cd".repeat(32)
    @Test fun acceptsPrivateIpv4AndDecodesCredentials() {
        val config = WifiConfig.parse("192.168.1.10:8765$suffix")
        assertEquals(8765, config.port)
        assertEquals(32, config.pinBytes.size)
        assertEquals(0xab.toByte(), config.pinBytes[0])
        assertEquals(0xcd.toByte(), config.tokenBytes[0])
    }
    @Test fun rejectsNonLanAmbiguousOrMalformedEndpoints() {
        for (host in listOf("8.8.8.8", "127.0.0.1", "172.32.0.1", "192.168.1.999", "010.1.2.3", "example.com")) {
            assertThrows(IllegalArgumentException::class.java) { WifiConfig.parse("$host:8765$suffix") }
        }
        assertThrows(IllegalArgumentException::class.java) { WifiConfig.parse("192.168.1.2:80$suffix") }
        assertThrows(IllegalArgumentException::class.java) { WifiConfig.parse("192.168.1.2:8765|bad|bad") }
    }
}
