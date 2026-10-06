package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class WifiDiscoveryTest {
    private val config = WifiConfig("192.168.1.8", 8765, "a".repeat(64), (0..31).joinToString("") { "%02x".format(it) })
    private val nonce = "b".repeat(32)
    @Test fun acceptsOnlyPairedIdentityWithFreshNonceAndPrivateAddress() {
        val body = "HRHERE1|$nonce|${config.pin}|8765"
        val response = "$body|${WifiDiscovery.signature(config, body)}"
        assertEquals("10.0.0.7", WifiDiscovery.response(config, nonce, "10.0.0.7", response)?.host)
        assertNull(WifiDiscovery.response(config, "c".repeat(32), "10.0.0.7", response))
        assertNull(WifiDiscovery.response(config, nonce, "8.8.8.8", response))
        assertNull(WifiDiscovery.response(config, nonce, "10.0.0.7", response.dropLast(1)+"z"))
        assertNull(WifiDiscovery.response(config.copy(pin="c".repeat(64)), nonce, "10.0.0.7", response))
        assertFalse(String(WifiDiscovery.request(config, nonce)).contains(config.token))
    }
    @Test fun bridgeDiscoveryCannotAcceptHeartReplyOrDifferentNonce() {
        val bridge = config.copy(port = 8767)
        val body = "HRBRIDGEHERE1|$nonce|${bridge.pin}|8767"
        val reply = "$body|${WifiDiscovery.signature(bridge, body)}"
        assertEquals("10.0.0.9", WifiDiscovery.response(bridge, nonce, "10.0.0.9", reply, "HRBRIDGEHERE1")?.host)
        assertNull(WifiDiscovery.response(bridge, nonce, "10.0.0.9", reply))
        assertNull(WifiDiscovery.response(bridge, "c".repeat(32), "10.0.0.9", reply, "HRBRIDGEHERE1"))
        assertFalse(String(WifiDiscovery.request(bridge, nonce, "HRBRIDGEDISC1")).contains(bridge.token))
    }
}
