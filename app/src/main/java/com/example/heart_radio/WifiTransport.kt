package com.example.heart_radio

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import java.net.InetSocketAddress
import java.net.InetAddress
import java.net.DatagramPacket
import java.net.MulticastSocket
import android.net.Network
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.LinkedBlockingDeque
import javax.net.ssl.*

/** Reuse the paired identity, try the saved address, then rediscover on Wi-Fi. */
class WifiTransport(private val context: Context, private val config: WifiConfig) : BleTransport {
    private val handler = Handler(Looper.getMainLooper())
    private val queue = LinkedBlockingDeque<ByteArray>(8)
    private val socketLock = Any()
    @Volatile private var active = false
    @Volatile private var ready = false
    private var socket: SSLSocket? = null
    private var discoverySocket: MulticastSocket? = null
    private var worker: Thread? = null
    private var sequence = 0
    private val session = java.security.SecureRandom().nextInt()
    private val heartbeat = object : Runnable {
        override fun run() {
            if (!active) return
            publish(2)
            handler.postDelayed(this, 5000)
        }
    }
    private fun post(kind: String, fields: Map<String, Any?> = emptyMap(), status: String? = null) {
        handler.post { if (active) {
            if (status != null) Probe.wifiStatus = status
            Probe.log(context, kind, fields)
        } }
    }
    override fun start() {
        if (active) return
        active = true
        Probe.wifiStatus = "Wi-Fi 正在连接电脑"
        Probe.log(context, "wifi_start", mapOf("host" to config.host, "port" to config.port, "tls" to true))
        handler.post(heartbeat)
        worker = Thread({ runConnection() }, "wifi-probe").also { it.start() }
    }
    private fun runConnection() {
        val trust = object : X509TrustManager {
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) { throw CertificateException("client certificate unsupported") }
            override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                val cert = chain?.firstOrNull() ?: throw CertificateException("no server certificate")
                cert.checkValidity()
                val digest = MessageDigest.getInstance("SHA-256").digest(cert.encoded)
                if (!MessageDigest.isEqual(digest, config.pinBytes)) throw CertificateException("computer certificate mismatch")
            }
        }
        val tls = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), null) }
        var attempt = 0
        var failures = 0
        var endpoint = config
        var needsDiscovery = false
        while (active) {
            attempt++
            try {
                val manager = context.getSystemService(ConnectivityManager::class.java)
                val wifi = manager.allNetworks.firstOrNull { manager.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true }
                    ?: throw java.io.IOException("没有可用Wi-Fi网络")
                if (needsDiscovery) {
                    post("wifi_discovery", status = "正在寻找已配对电脑")
                    endpoint = discover(wifi) ?: endpoint
                }
                val client = tls.socketFactory.createSocket() as SSLSocket
                synchronized(socketLock) { if (!active) { client.close(); return }; socket = client }
                wifi.bindSocket(client)
                client.soTimeout = 4000
                client.enabledProtocols = arrayOf("TLSv1.2")
                client.connect(InetSocketAddress(endpoint.host, endpoint.port), 4000)
                client.startHandshake()
                val output = client.outputStream
                val input = client.inputStream
                output.write(config.tokenBytes); output.flush()
                if (input.read() != 1) throw java.io.IOException("接收端身份校验未通过")
                if (!active) return
                failures = 0; needsDiscovery = false
                context.getSharedPreferences("wifi", Context.MODE_PRIVATE).edit()
                    .putString("connection_code", "${endpoint.host}:${endpoint.port}|${config.pin}|${config.token}").apply()
                queue.clear(); ready = true
                post("wifi_connected", mapOf("attempt" to attempt, "host" to endpoint.host, "port" to endpoint.port), "Wi-Fi 已连接电脑")
                handler.post { if (active && ready) publish(2) }
                while (active) {
                    val packet = queue.poll(1, java.util.concurrent.TimeUnit.SECONDS) ?: continue
                    output.write(packet); output.flush()
                    if (input.read() != 1) throw java.io.IOException("电脑确认失败或连接关闭")
                    post("wifi_ack", mapOf("hex" to packet.hex()), "Wi-Fi 发送正常（电脑已确认）")
                }
            } catch (e: Exception) {
                failures = (failures + 1).coerceAtMost(5)
                needsDiscovery = true
                if (active) post("wifi_error", mapOf("attempt" to attempt, "error" to e.javaClass.simpleName,
                    "message" to e.message), "Wi-Fi 暂未连接，将自动寻找电脑并重试")
            } finally {
                ready = false; queue.clear()
                synchronized(socketLock) { try { socket?.close() } catch (_: Exception) { }; socket = null }
            }
            if (active) try { Thread.sleep(minOf(30000L, 3000L shl (failures.coerceAtLeast(1) - 1))) } catch (_: InterruptedException) { return }
        }
    }
    private fun discover(wifi: Network): WifiConfig? {
        val nonce = ByteArray(16).also { java.security.SecureRandom().nextBytes(it) }.hex()
        val data = WifiDiscovery.request(config, nonce)
        val probe = MulticastSocket(null)
        synchronized(socketLock) { if (!active) { probe.close(); return null }; discoverySocket = probe }
        try {
            wifi.bindSocket(probe)
            probe.bind(InetSocketAddress(0))
            probe.timeToLive = 1
            probe.soTimeout = 500
            val target = InetAddress.getByName(WifiDiscovery.GROUP)
            // Short discovery window; responses are unicast, no multicast or wake lock is held on the watch.
            repeat(2) {
                if (!active) return null
                probe.send(DatagramPacket(data, data.size, target, config.port))
                val until = SystemClock.elapsedRealtime() + 1000
                while (active && SystemClock.elapsedRealtime() < until) {
                    val packet = DatagramPacket(ByteArray(512), 512)
                    try { probe.receive(packet) } catch (_: java.net.SocketTimeoutException) { continue }
                    val found = WifiDiscovery.response(config, nonce, packet.address.hostAddress ?: continue,
                        String(packet.data, packet.offset, packet.length, Charsets.US_ASCII))
                    if (found != null) return found
                }
            }
            return null
        } finally {
            synchronized(socketLock) { probe.close(); if (discoverySocket === probe) discoverySocket = null }
        }
    }
    override fun publish(kind: Int) {
        if (!active || !ready) return
        val st = Probe.state
        val packet = BlePacket.encode(kind, session, sequence++, st.lastWallMs,
            st.recentValue(SystemClock.elapsedRealtimeNanos()), st.accuracy, st.running, st.registered)
        if (!queue.offer(packet)) { queue.poll(); queue.offer(packet); Probe.log(context, "wifi_queue_drop") }
        Probe.log(context, "wifi_packet", mapOf("packetKind" to kind, "hex" to packet.hex()))
    }
    override fun close(updateStatus: Boolean) {
        active = false; ready = false
        handler.removeCallbacksAndMessages(null); queue.clear()
        synchronized(socketLock) { try { socket?.close() } catch (_: Exception) { }; socket = null; discoverySocket?.close(); discoverySocket = null }
        worker?.interrupt(); worker = null
        if (updateStatus) Probe.wifiStatus = "Wi-Fi 已停止"
        Probe.log(context, "wifi_stop")
    }
    private fun ByteArray.hex() = joinToString("") { "%02x".format(it.toInt() and 255) }
}
