package com.example.heart_radio

import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Wire v1: exactly 20 bytes; status packets retain the last sensor receipt time. */
object BlePacket {
    fun encode(kind: Int, session: Int, sequence: Int, receiptMs: Long, bpm: Float?, accuracy: Int,
               running: Boolean, registered: Boolean): ByteArray {
        val valid = bpm != null && bpm.isFinite() && bpm > 0 && bpm <= 6553.5f
        val flags = (if (valid) 1 else 0) or (if (running) 2 else 0) or (if (registered) 4 else 0)
        return ByteBuffer.allocate(20).order(ByteOrder.LITTLE_ENDIAN)
            .put(1).put(kind.toByte()).putInt(session).putShort(sequence.toShort()).putLong(receiptMs)
            .putShort(if (valid) kotlin.math.round(bpm!! * 10).toInt().toShort() else 0.toShort())
            .put(accuracy.coerceIn(-128, 127).toByte()).put(flags.toByte()).array()
    }
}
