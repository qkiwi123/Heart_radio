package com.example.heart_radio

import android.annotation.SuppressLint
import android.bluetooth.*
import android.bluetooth.le.*
import android.content.*
import android.os.Build
import android.os.ParcelUuid

/** PC initiates/maintains the radio link; watch is the GATT client for payload writes. */
@SuppressLint("MissingPermission")
class BleDuplex(private val context: Context, private val address: String) : BleTransport {
    private val writer = BleCentral(context, address)
    private var links = 0
    private var active = false
    private var advertiser: BluetoothLeAdvertiser? = null
    private var registered = false
    private var linked = false
    private fun link(device: BluetoothDevice, connected: Boolean) {
        if (!active || !device.address.equals(address, ignoreCase = true)) return
        if (connected && !linked && device.bondState == BluetoothDevice.BOND_BONDED && links < 3) {
            linked = true; links++
            Probe.log(context, "ble_duplex_link", mapOf("connected" to true, "attempt" to links))
            writer.start()
        } else if (!connected && linked) {
            linked = false; writer.close(false)
            Probe.bleStatus = if (links < 3) "电脑链路断开，等待重连" else "本次连接次数已满，请重新连接"
            Probe.log(context, "ble_duplex_link", mapOf("connected" to false, "attempt" to links))
        }
    }
    private val receiver = object : BroadcastReceiver() {
        @Suppress("DEPRECATION")
        override fun onReceive(c: Context, intent: Intent) {
            val device = intent.getParcelableExtra<BluetoothDevice>(BluetoothDevice.EXTRA_DEVICE) ?: return
            link(device, intent.action == BluetoothDevice.ACTION_ACL_CONNECTED)
        }
    }
    private val callback = object : AdvertiseCallback() {
        override fun onStartSuccess(settings: AdvertiseSettings) {
            if (!active) { advertiser?.stopAdvertising(this); return }
            Probe.bleStatus = "蓝牙广播中，等待电脑连接"
            Probe.log(context, "ble_discovery_advertising", mapOf("success" to true, "watchGattServer" to false))
            val manager = context.getSystemService(BluetoothManager::class.java)
            val device = manager.adapter.getRemoteDevice(address)
            if (manager.getConnectionState(device, BluetoothProfile.GATT) == BluetoothProfile.STATE_CONNECTED) link(device, true)
        }
        override fun onStartFailure(errorCode: Int) {
            if (active) { Probe.bleStatus = "蓝牙广播失败：$errorCode"; close(false) }
        }
    }
    override fun start() {
        if (active) return
        active = true
        try {
            val adapter = context.getSystemService(BluetoothManager::class.java).adapter
            if (adapter?.isEnabled != true || adapter.getRemoteDevice(address).bondState != BluetoothDevice.BOND_BONDED) {
                Probe.bleStatus = "请开启蓝牙并先与电脑配对"; close(false); return
            }
            val filter = IntentFilter().apply { addAction(BluetoothDevice.ACTION_ACL_CONNECTED); addAction(BluetoothDevice.ACTION_ACL_DISCONNECTED) }
            if (Build.VERSION.SDK_INT >= 33) context.registerReceiver(receiver, filter, Context.RECEIVER_EXPORTED)
            else context.registerReceiver(receiver, filter)
            registered = true
            advertiser = adapter.bluetoothLeAdvertiser
            if (advertiser == null) { Probe.bleStatus = "无法开始连接广播"; close(false); return }
            // Only a discovery marker; the payload GATT service lives on the PC.
            advertiser!!.startAdvertising(AdvertiseSettings.Builder().setConnectable(true)
                .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_POWER).build(),
                AdvertiseData.Builder().addServiceUuid(ParcelUuid(BlePeripheral.SERVICE)).build(), callback)
        } catch (e: Exception) { Probe.bleStatus = "蓝牙启动异常：${e.javaClass.simpleName}"; close(false) }
    }
    override fun publish(kind: Int) { writer.publish(kind) }
    override fun close(updateStatus: Boolean) {
        active = false; linked = false; writer.close(false)
        try { advertiser?.stopAdvertising(callback) } catch (_: Exception) { }
        if (registered) { context.unregisterReceiver(receiver); registered = false }
        if (updateStatus) Probe.bleStatus = "蓝牙已停止"
        Probe.log(context, "ble_duplex_stop")
    }
}
