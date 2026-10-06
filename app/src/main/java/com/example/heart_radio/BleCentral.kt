package com.example.heart_radio

import android.annotation.SuppressLint
import android.bluetooth.*
import android.content.Context
import android.os.*
import java.util.UUID

/** Connect only to the explicitly selected, already bonded PC; no scan or location access. */
@SuppressLint("MissingPermission")
class BleCentral(private val context: Context, private val address: String) : BleTransport {
    private val handler = Handler(Looper.getMainLooper())
    private var gatt: BluetoothGatt? = null
    private var data: BluetoothGattCharacteristic? = null
    private var active = false
    private var ready = false
    private var busy = false
    private var attempts = 0
    private var sequence = 0
    private val session = java.security.SecureRandom().nextInt()
    private val queue = java.util.ArrayDeque<ByteArray>()
    private val timeout = Runnable { if (active) retry("连接或写入超时") }
    private val rediscover = Runnable {
        if (active && !ready && gatt?.discoverServices() != true) retry("服务查询未提交")
    }
    private val linkHeartbeat = object : Runnable {
        override fun run() {
            if (!active || !ready) return
            publish(2)
            handler.postDelayed(this, 5000)
        }
    }
    private fun report(message: String, fields: Map<String, Any?> = emptyMap()) {
        Probe.bleStatus = message
        Probe.log(context, "ble_status", fields + mapOf("message" to message, "role" to "central", "bleSession" to (session.toLong() and 0xffffffffL)))
    }
    private val callback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(client: BluetoothGatt, status: Int, newState: Int) { handler.post {
            if (!active || client !== gatt) return@post
            Probe.log(context, "ble_connection", mapOf("role" to "central", "status" to status, "state" to newState))
            if (status == BluetoothGatt.GATT_SUCCESS && newState == BluetoothProfile.STATE_CONNECTED) {
                report("已连接电脑，查询接收服务")
                if (!client.discoverServices()) retry("服务查询未提交")
            } else if (status != BluetoothGatt.GATT_SUCCESS || newState == BluetoothProfile.STATE_DISCONNECTED) retry("电脑连接断开：$status")
        } }
        override fun onServicesDiscovered(client: BluetoothGatt, status: Int) { handler.post {
            if (!active || client !== gatt) return@post
            data = if (status == BluetoothGatt.GATT_SUCCESS) client.getService(SERVICE)?.getCharacteristic(DATA) else null
            Probe.log(context, "ble_service_discovery", mapOf("status" to status, "found" to (data != null)))
            if (data == null) {
                if (status != BluetoothGatt.GATT_SUCCESS) { retry("电脑服务查询失败：$status"); return@post }
                // The PC may still be starting its receiver. Keep this connection and the
                // original 25-second deadline instead of consuming all radio-link attempts.
                report("等待电脑接收服务，请先启动电脑接收程序")
                handler.removeCallbacks(rediscover)
                handler.postDelayed(rediscover, 3000)
                return@post
            }
            handler.removeCallbacks(rediscover)
            handler.removeCallbacks(timeout); ready = true
            report("电脑服务就绪，等待发送确认")
            publish(2)
            handler.removeCallbacks(linkHeartbeat)
            handler.postDelayed(linkHeartbeat, 5000)
        } }
        override fun onCharacteristicWrite(client: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int) { handler.post {
            if (!active || client !== gatt || characteristic.uuid != DATA) return@post
            handler.removeCallbacks(timeout); busy = false
            Probe.log(context, "ble_write_result", mapOf("status" to status))
            if (status != BluetoothGatt.GATT_SUCCESS) retry("电脑写入失败：$status")
            else { Probe.bleStatus = "蓝牙发送正常（电脑已确认）"; drain() }
        } }
    }
    override fun start() {
        if (active) return
        active = true; attempts = 0
        connect()
    }
    private fun connect() {
        if (!active) return
        try {
            val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter
            if (adapter?.isEnabled != true || !BluetoothAdapter.checkBluetoothAddress(address)) {
                report("蓝牙未开启或电脑地址无效"); close(false); return
            }
            val device = adapter.getRemoteDevice(address)
            if (device.bondState != BluetoothDevice.BOND_BONDED) { report("请先在系统中与此电脑配对"); close(false); return }
            attempts++
            report("正在连接已配对电脑（$attempts/3）", mapOf("target" to address))
            gatt = device.connectGatt(context, false, callback, BluetoothDevice.TRANSPORT_LE)
            handler.postDelayed(timeout, 25_000)
        } catch (e: Exception) { report("连接异常：${e.javaClass.simpleName}: ${e.message}"); close(false) }
    }
    private fun retry(reason: String) {
        handler.removeCallbacks(timeout)
        handler.removeCallbacks(rediscover)
        handler.removeCallbacks(linkHeartbeat)
        val old = gatt; gatt = null; ready = false; busy = false; data = null; queue.clear()
        try { old?.disconnect(); old?.close() } catch (_: Exception) { }
        if (active && attempts < 3) { report("$reason；稍后重连"); handler.postDelayed({ connect() }, 3000) }
        else { report("$reason；本次重试已结束"); active = false }
    }
    override fun publish(kind: Int) {
        if (!active || !ready) return
        val st = Probe.state
        val packet = BlePacket.encode(kind, session, sequence++, st.lastWallMs,
            st.recentValue(SystemClock.elapsedRealtimeNanos()), st.accuracy, st.running, st.registered)
        if (queue.size >= 8) { queue.removeFirst(); Probe.log(context, "ble_queue_drop") }
        queue.add(packet)
        Probe.log(context, "ble_packet", mapOf("role" to "central", "packetKind" to kind,
            "hex" to packet.joinToString("") { "%02x".format(it.toInt() and 255) }))
        drain()
    }
    @Suppress("DEPRECATION")
    private fun drain() {
        if (!ready || busy || queue.isEmpty()) return
        val characteristic = data ?: return
        try {
            characteristic.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
            characteristic.value = queue.removeFirst()
            busy = true
            if (gatt?.writeCharacteristic(characteristic) != true) retry("写入未提交")
            else handler.postDelayed(timeout, 10_000)
        } catch (e: Exception) { retry("写入异常：${e.javaClass.simpleName}") }
    }
    override fun close(updateStatus: Boolean) {
        active = false; ready = false; busy = false; handler.removeCallbacksAndMessages(null); queue.clear()
        val old = gatt; gatt = null; data = null
        try { old?.disconnect(); old?.close() } catch (_: Exception) { }
        if (updateStatus) report("蓝牙已停止")
    }
    companion object {
        val SERVICE: UUID = UUID.fromString("749b2000-6f67-4d32-96dd-4d778c27a101")
        val DATA: UUID = UUID.fromString("749b2001-6f67-4d32-96dd-4d778c27a101")
    }
}
