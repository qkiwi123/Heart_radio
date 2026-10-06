package com.example.heart_radio

import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/** Authenticated LAN discovery. The shared secret is never sent in a datagram. */
object WifiDiscovery {
    const val GROUP = "239.255.72.82"
    fun signature(config: WifiConfig, body: String): String = Mac.getInstance("HmacSHA256").run {
        init(SecretKeySpec(config.tokenBytes, "HmacSHA256"))
        doFinal(body.toByteArray(Charsets.US_ASCII)).joinToString("") { "%02x".format(it.toInt() and 255) }
    }
    fun request(config: WifiConfig, nonce: String, prefix: String = "HRDISC1"): ByteArray {
        val body = "$prefix|$nonce|${config.pin}"
        return "$body|${signature(config, body)}".toByteArray(Charsets.US_ASCII)
    }
    fun response(config: WifiConfig, nonce: String, host: String, text: String, prefix: String = "HRHERE1"): WifiConfig? {
        val fields = text.split('|')
        if (fields.size != 5 || fields[0] != prefix || fields[1] != nonce || fields[2] != config.pin) return null
        val body = fields.take(4).joinToString("|")
        if (!MessageDigest.isEqual(fields[4].toByteArray(Charsets.US_ASCII), signature(config, body).toByteArray(Charsets.US_ASCII))) return null
        return try { WifiConfig.parse("$host:${fields[3]}|${config.pin}|${config.token}") } catch (_: IllegalArgumentException) { null }
    }
}
