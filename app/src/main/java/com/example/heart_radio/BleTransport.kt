package com.example.heart_radio

interface BleTransport {
    fun start()
    fun publish(kind: Int)
    fun close(updateStatus: Boolean = true)
}
