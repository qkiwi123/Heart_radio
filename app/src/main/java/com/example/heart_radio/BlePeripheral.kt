package com.example.heart_radio

import android.annotation.SuppressLint
import android.bluetooth.*
import android.bluetooth.le.*
import android.content.Context
import android.os.*
import java.util.UUID

/** All state and Android GATT callback handling are serialized on the service main looper. */
@SuppressLint("MissingPermission")
class BlePeripheral(private val context: Context,
                    private val linkChanged: ((BluetoothDevice, Boolean) -> Unit)? = null) : BleTransport {
    private val handler = Handler(Looper.getMainLooper())
    private var server: BluetoothGattServer? = null
    private var advertiser: BluetoothLeAdvertiser? = null
    private var peer: BluetoothDevice? = null
    private val connected = mutableSetOf<BluetoothDevice>()
    private var subscribed = false
    private var active = false
    private var busy = false
    private val queue = java.util.ArrayDeque<ByteArray>()
    private var sequence = 0
    private val session = java.security.SecureRandom().nextInt()
    private val characteristic = BluetoothGattCharacteristic(DATA,
        BluetoothGattCharacteristic.PROPERTY_NOTIFY, 0).apply {
        addDescriptor(BluetoothGattDescriptor(CCC,
            BluetoothGattDescriptor.PERMISSION_READ or BluetoothGattDescriptor.PERMISSION_WRITE_ENCRYPTED))
    }
    private fun report(message: String, fields: Map<String, Any?> = emptyMap()) {
        Probe.bleStatus = message
        Probe.log(context, "ble_status", fields + mapOf("message" to message, "bleSession" to (session.toLong() and 0xffffffffL)))
    }
    private fun respond(device: BluetoothDevice, requestId: Int, status: Int, offset: Int, data: ByteArray?) {
        val submitted = server?.sendResponse(device, requestId, status, offset, data) == true
        Probe.log(context, "ble_response", mapOf("requestId" to requestId, "status" to status,
            "offset" to offset, "bytes" to (data?.size ?: 0), "submitted" to submitted))
    }
    private val timeout = Runnable {
        if (active && busy) { report("蓝牙通知超时；断开后可重连"); peer?.let { server?.cancelConnection(it) }; busy = false; queue.clear() }
    }
    private val advertiseCallback = object : AdvertiseCallback() {
        override fun onStartSuccess(settings: AdvertiseSettings) { handler.post {
            if (active) report("蓝牙广播中，等待电脑连接", mapOf("advertiseSuccess" to true))
            else advertiser?.stopAdvertising(this)
        } }
        override fun onStartFailure(errorCode: Int) { handler.post {
            if (active) { report("蓝牙广播失败：$errorCode", mapOf("advertiseError" to errorCode)); close(false) }
        } }
    }
    private val callback = object : BluetoothGattServerCallback() {
        override fun onServiceAdded(status: Int, service: BluetoothGattService) { handler.post {
            if (!active) return@post
            if (status != BluetoothGatt.GATT_SUCCESS) { report("GATT 服务添加失败：$status"); close(false); return@post }
            try {
                advertiser!!.startAdvertising(AdvertiseSettings.Builder().setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_POWER)
                    .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_MEDIUM).setConnectable(true).setTimeout(0).build(),
                    AdvertiseData.Builder().addServiceUuid(ParcelUuid(SERVICE)).setIncludeDeviceName(false).build(), advertiseCallback)
            } catch (e: Exception) { report("广播异常：${e.javaClass.simpleName}: ${e.message}"); close(false) }
        } }
        override fun onConnectionStateChange(device: BluetoothDevice, status: Int, newState: Int) { handler.post {
            if (!active) return@post
            Probe.log(context, "ble_connection", mapOf("status" to status, "state" to newState))
            if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                connected.add(device)
                if (!subscribed) report("蓝牙设备已连接，等待加密订阅")
                linkChanged?.invoke(device, true)
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                connected.remove(device)
                if (peer == device) {
                    peer = null; subscribed = false; busy = false; queue.clear(); handler.removeCallbacks(timeout)
                }
                if (peer == null) report("蓝牙已断开，等待重新连接")
                linkChanged?.invoke(device, false)
            }
        } }
        override fun onDescriptorReadRequest(device: BluetoothDevice, requestId: Int, offset: Int, descriptor: BluetoothGattDescriptor) { handler.post {
            if (!active) return@post
            Probe.log(context, "ble_ccc_read", mapOf("offset" to offset))
            val ok = device in connected && descriptor.uuid == CCC && offset == 0
            respond(device, requestId, if (ok) BluetoothGatt.GATT_SUCCESS else BluetoothGatt.GATT_FAILURE, offset,
                if (subscribed && device == peer) BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE else BluetoothGattDescriptor.DISABLE_NOTIFICATION_VALUE)
        } }
        override fun onDescriptorWriteRequest(device: BluetoothDevice, requestId: Int, descriptor: BluetoothGattDescriptor,
            preparedWrite: Boolean, responseNeeded: Boolean, offset: Int, value: ByteArray) { handler.post {
            if (!active) return@post
            val enable = value.contentEquals(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
            val disable = value.contentEquals(BluetoothGattDescriptor.DISABLE_NOTIFICATION_VALUE)
            Probe.log(context, "ble_ccc_write", mapOf("offset" to offset, "prepared" to preparedWrite, "enable" to enable, "bondState" to device.bondState))
            val ok = device in connected && (peer == null || peer == device) && descriptor.uuid == CCC && !preparedWrite && offset == 0 && (enable || disable)
            if (ok) { subscribed = enable; peer = if (enable) device else null; if (disable) { queue.clear(); busy = false; handler.removeCallbacks(timeout) } }
            if (responseNeeded) respond(device, requestId, if (ok) BluetoothGatt.GATT_SUCCESS else BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, offset, null)
            if (ok) { report(if (enable) "蓝牙已订阅，正在发送" else "电脑已取消订阅"); if (enable) publish(2) }
        } }
        override fun onMtuChanged(device: BluetoothDevice, mtu: Int) { handler.post {
            if (active) Probe.log(context, "ble_mtu", mapOf("mtu" to mtu))
        } }
        override fun onCharacteristicReadRequest(device: BluetoothDevice, requestId: Int, offset: Int, characteristic: BluetoothGattCharacteristic) { handler.post {
            if (!active) return@post
            Probe.log(context, "ble_read", mapOf("offset" to offset, "bondState" to device.bondState))
            // Heart-rate payloads are notification-only, after an encrypted CCC subscription.
            respond(device, requestId, BluetoothGatt.GATT_READ_NOT_PERMITTED, offset, null)
        } }
        override fun onCharacteristicWriteRequest(device: BluetoothDevice, requestId: Int, characteristic: BluetoothGattCharacteristic,
            preparedWrite: Boolean, responseNeeded: Boolean, offset: Int, value: ByteArray) { handler.post {
            if (active && responseNeeded) respond(device, requestId, BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, offset, null)
        } }
        override fun onExecuteWrite(device: BluetoothDevice, requestId: Int, execute: Boolean) { handler.post {
            if (active) respond(device, requestId, BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, 0, null)
        } }
        override fun onNotificationSent(device: BluetoothDevice, status: Int) { handler.post {
            if (!active || device != peer) return@post
            handler.removeCallbacks(timeout); busy = false
            Probe.log(context, "ble_notification_result", mapOf("status" to status))
            if (status != BluetoothGatt.GATT_SUCCESS) { report("蓝牙发送失败：$status"); queue.clear(); server?.cancelConnection(device) }
            else drain()
        } }
    }
    override fun start() {
        if (active) return
        try {
            val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter
            val enabled = adapter?.isEnabled == true
            val multiple = adapter?.isMultipleAdvertisementSupported == true
            Probe.log(context, "ble_capability", mapOf("adapter" to (adapter != null), "enabled" to enabled, "multipleAdvertisementSupported" to multiple))
            if (!enabled) { report("蓝牙未开启，请在手表设置开启"); return }
            advertiser = adapter?.bluetoothLeAdvertiser
            if (advertiser == null) { report("系统未提供 BLE 广播器"); return }
            active = true
            server = context.getSystemService(BluetoothManager::class.java).openGattServer(context, callback)
            if (server == null) { report("无法创建 GATT 服务"); close(false); return }
            val service = BluetoothGattService(SERVICE, BluetoothGattService.SERVICE_TYPE_PRIMARY).apply { addCharacteristic(characteristic) }
            report("正在创建蓝牙服务")
            if (server?.addService(service) != true) { report("GATT addService 返回 false"); close(false) }
        } catch (e: Exception) { report("蓝牙异常：${e.javaClass.simpleName}: ${e.message}"); close(false) }
    }
    override fun publish(kind: Int) {
        if (!active || !subscribed || peer == null) return
        val st = Probe.state
        val packet = BlePacket.encode(kind, session, sequence++, st.lastWallMs,
            st.recentValue(SystemClock.elapsedRealtimeNanos()), st.accuracy, st.running, st.registered)
        if (queue.size >= 8) { queue.removeFirst(); Probe.log(context, "ble_queue_drop") }
        queue.add(packet)
        Probe.log(context, "ble_packet", mapOf("hex" to packet.joinToString("") { "%02x".format(it.toInt() and 255) }, "packetKind" to kind))
        drain()
    }
    @Suppress("DEPRECATION")
    private fun drain() {
        if (busy || !subscribed || queue.isEmpty()) return
        val device = peer ?: return
        val data = queue.removeFirst()
        try {
            characteristic.value = data
            busy = true
            if (server?.notifyCharacteristicChanged(device, characteristic, false) != true) {
                busy = false; report("通知提交失败"); queue.clear(); server?.cancelConnection(device)
            } else handler.postDelayed(timeout, 10_000)
        } catch (e: Exception) { busy = false; report("通知异常：${e.javaClass.simpleName}"); queue.clear(); server?.cancelConnection(device) }
    }
    override fun close(updateStatus: Boolean) {
        active = false; subscribed = false; busy = false; queue.clear(); handler.removeCallbacksAndMessages(null)
        try { advertiser?.stopAdvertising(advertiseCallback) } catch (_: Exception) { }
        try { server?.close() } catch (_: Exception) { }
        connected.clear(); peer = null; server = null
        if (updateStatus) report("蓝牙已停止")
    }
    companion object {
        val SERVICE: UUID = UUID.fromString("749b1000-6f67-4d32-96dd-4d778c27a101")
        val DATA: UUID = UUID.fromString("749b1001-6f67-4d32-96dd-4d778c27a101")
        val CCC: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    }
}
