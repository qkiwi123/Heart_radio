package com.example.heart_radio

import android.app.*
import android.content.Intent
import android.net.*
import android.os.*
import android.provider.Settings
import org.json.JSONObject
import java.io.*
import java.net.InetSocketAddress
import java.net.InetAddress
import java.net.DatagramPacket
import java.net.MulticastSocket
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import javax.net.ssl.*

/** Explicit standby with a saved duration. Reconnects transport only; never replays a measurement. */
class CapabilityBridgeService : Service() {
    companion object {
        @Volatile var status = "接口待命未启动"
        @Volatile private var snapshot = JSONObject().put("state", "stopped")
        @Volatile private var liveService: CapabilityBridgeService? = null
        fun isRunning(): Boolean = liveService?.active == true
        fun connected(): Boolean = liveService?.let{it.active&&snapshot.optString("state")=="connected"&&(!it.managedConnection||SystemClock.elapsedRealtime()-it.lastPeerAt<45_000)}==true
        fun state(): JSONObject = liveService?.liveReport() ?: JSONObject(snapshot.toString()).put("leaseRemainingMs", 0)
        fun duration(context: android.content.Context): Long = context.getSharedPreferences("capability-standby", MODE_PRIVATE)
            .getLong("durationMs", BridgeLease.DEFAULT_MS)
        fun followsComputer(context: android.content.Context): Boolean = context.getSharedPreferences("capability-standby", MODE_PRIVATE)
            .getBoolean("followComputer", false)
        fun saveDuration(context: android.content.Context, durationMs: Long, follow: Boolean = followsComputer(context)): Boolean {
            require(durationMs == 0L || durationMs in 1000..BridgeLease.MAX_MS)
            val service = liveService?.takeIf { it.active }
            val next = service?.takeIf { !follow }?.let { BridgeLease.start(SystemClock.elapsedRealtime(), System.currentTimeMillis(), it.bootCount(), durationMs) }
            val editor = context.getSharedPreferences("capability-standby", MODE_PRIVATE).edit().putLong("durationMs", durationMs).putBoolean("followComputer", follow)
            if (!follow) editor.remove("syncRevision").remove("syncInstanceId")
            if (next != null) editor.putLong("started", next.startedElapsedMs).putLong("deadline", next.deadlineElapsedMs)
                .putLong("wallDeadline", next.deadlineWallMs).putLong("leaseDurationMs", next.durationMs).putInt("boot", next.bootCount)
            if (!editor.commit()) return false
            Probe.log(context, "standby_duration_saved", mapOf("durationMs" to durationMs, "unlimited" to (durationMs == 0L), "followComputer" to follow, "appliedToRunning" to (next != null)))
            if (service != null) {
                if (!follow || !service.followComputer) { service.syncRevision = ""; service.syncInstanceId = "" }
                service.followComputer = follow
            }
            if (service != null && next != null) {
                service.lease = next
                service.handler.removeCallbacks(service.expiry)
                if (durationMs != 0L) service.handler.post(service.expiry)
                service.transition(snapshot.optString("state", "connected"), "duration_saved")
                synchronized(service.retrySignal) { service.retrySignal.notifyAll() }
            }
            AgentInteraction.updateDuration(context)
            service?.publishState()
            return true
        }
    }
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var engine: CapabilityEngine
    private lateinit var manager: ConnectivityManager
    private var callbackAdded = false
    private val retrySignal = Object()
    private val auditLock = Any()
    @Volatile private var active = false
    @Volatile private var socket: SSLSocket? = null
    @Volatile private var discoverySocket: MulticastSocket? = null
    @Volatile private var boundNetwork: Network? = null
    @Volatile private var lease: BridgeLease? = null
    @Volatile private var followComputer = false
    @Volatile private var syncRevision = ""
    @Volatile private var syncInstanceId = ""
    @Volatile private var reports: LinkedBlockingQueue<JSONObject>? = null
    private var worker: Thread? = null
    private var managedConnection = false
    @Volatile private var lastPeerAt=0L
    private var receiptSelection=""
    private val receiptObserver:(JSONObject)->Unit={ row ->
        if(!managedConnection || ConnectionService.owns("wifi",receiptSelection))
            reports?.offer(JSONObject(row.toString()).put("id","reminder_receipt").put("selectionId",receiptSelection))
    }
    private var attempt = 0
    private var sessionId = ""
    private val expiry = object : Runnable {
        override fun run() {
            if (!active || lease?.durationMs == 0L) return
            val left = remaining()
            if (left <= 0) finish("lease_expired") else handler.postDelayed(this, minOf(left, 10_000))
        }
    }
    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) { synchronized(retrySignal) { retrySignal.notifyAll() } }
        override fun onLost(network: Network) {
            if (network == boundNetwork) { closeSocket(); discoverySocket?.close() }
            synchronized(retrySignal) { retrySignal.notifyAll() }
        }
    }
    override fun onBind(intent: Intent?) = null
    override fun onCreate() {
        super.onCreate()
        engine = DeviceGateway.engine(this)
        manager = getSystemService(ConnectivityManager::class.java)
        liveService = this
        ReminderEngine.subscribe(receiptObserver)
    }
    private fun bootCount() = try { Settings.Global.getInt(contentResolver, Settings.Global.BOOT_COUNT, -1) }
        catch (_: Exception) { -1 }
    private fun remaining(): Long {
        if (managedConnection) return Long.MAX_VALUE
        val value = lease ?: return 0
        if (value.durationMs == 0L && value.bootCount < 0) return Long.MAX_VALUE
        // Unknown boot count permits the current instance, but never restoration after process death.
        return if (value.bootCount < 0) minOf(value.deadlineElapsedMs - SystemClock.elapsedRealtime(),
            value.deadlineWallMs - System.currentTimeMillis()).coerceIn(0, BridgeLease.MAX_MS)
        else value.remaining(SystemClock.elapsedRealtime(), System.currentTimeMillis(), bootCount())
    }
    private fun liveReport(): JSONObject = JSONObject(snapshot.toString())
        .put("leaseRemainingMs", if (active && lease?.durationMs == 0L) JSONObject.NULL else if (active) remaining() else 0)
        .put("leaseUnlimited", active && lease?.durationMs == 0L)
        .put("leaseDurationMs", lease?.durationMs ?: duration(this))
        .put("durationSyncVersion", 1).put("followComputer", followComputer)
        .put("durationSyncRevision", syncRevision).put("durationSyncInstanceId", syncInstanceId)
        .put("managedConnection",managedConnection).put("connectionControl",ConnectionService.state())
        .put("agentInteraction",AgentInteraction.state()).put("heartBroadcast",HeartBroadcast.state())
        .put("heartRunning",Probe.state.running).put("heartRegistered",Probe.state.registered).put("heartDiagnostic",HeartStream.diagnosticJson())
        .put("wallMs", System.currentTimeMillis()).put("elapsedMs", SystemClock.elapsedRealtime())
        .put("screenInteractive", Probe.screen(this))
    private fun notification(): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, if(managedConnection)MainActivity::class.java else CapabilityActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(this, 1, Intent(this, if(managedConnection)ConnectionService::class.java else CapabilityBridgeService::class.java).setAction("STOP"), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, "capability").setSmallIcon(android.R.drawable.ic_menu_info_details)
            .setContentTitle(if(managedConnection)"Wi-Fi连接待命" else if (lease?.durationMs == 0L) "接口待命（不限时）" else "接口待命（已设限时）").setContentText(status)
            .setContentIntent(open).addAction(Notification.Action.Builder(null, if(managedConnection)"断开连接" else "停止待命", stop).build()).build()
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if(ProjectSession.suspended){stopSelf(startId);return START_NOT_STICKY}
        if (intent?.action == "STOP") { if(managedConnection)ConnectionService.stop(this) else finish("user_stop"); return START_NOT_STICKY }
        if (active) return if (!managedConnection && (lease?.bootCount ?: -1) >= 0) START_STICKY else START_NOT_STICKY
        managedConnection = intent?.getBooleanExtra("managed_connection",false) == true
        val preferences = getSharedPreferences("capability-standby", MODE_PRIVATE)
        followComputer = preferences.getBoolean("followComputer", false)
        syncRevision = if (followComputer) preferences.getString("syncRevision", "") ?: "" else ""
        syncInstanceId = if (followComputer) preferences.getString("syncInstanceId", "") ?: "" else ""
        val restored = intent == null || flags and (START_FLAG_RETRY or START_FLAG_REDELIVERY) != 0
        if (!restored) {
            syncRevision = ""; syncInstanceId = ""
            preferences.edit().remove("syncRevision").remove("syncInstanceId").commit()
        }
        val value = if (restored) BridgeLease(preferences.getLong("started", -1), preferences.getLong("deadline", -1),
            preferences.getLong("wallDeadline", -1), preferences.getInt("boot", -1), preferences.getLong("leaseDurationMs", BridgeLease.DEFAULT_MS))
        else BridgeLease.start(SystemClock.elapsedRealtime(), System.currentTimeMillis(), bootCount(), if(managedConnection) 0 else duration(this))
        if (restored && (!preferences.getBoolean("enabled", false) || value.bootCount < 0 ||
                    value.remaining(SystemClock.elapsedRealtime(), System.currentTimeMillis(), bootCount()) <= 0)) {
            finish("expired_or_not_enabled"); return START_NOT_STICKY
        }
        lease = value
        if (!restored && !preferences.edit().putBoolean("enabled", true).putLong("started", value.startedElapsedMs)
            .putLong("deadline", value.deadlineElapsedMs).putLong("wallDeadline", value.deadlineWallMs)
            .putLong("leaseDurationMs", value.durationMs).putInt("boot", value.bootCount).commit()) {
            finish("configuration_error"); return START_NOT_STICKY
        }
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel("capability", "按需接口待命", NotificationManager.IMPORTANCE_LOW))
        status = "正在连接电脑；意外断连会自动重试"
        if (Build.VERSION.SDK_INT >= 34) {
            var types = android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE
            if (Probe.allowed(this)) types = types or android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_HEALTH
            if (checkSelfPermission("android.permission.ACCESS_FINE_LOCATION") == android.content.pm.PackageManager.PERMISSION_GRANTED)
                types = types or android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
            startForeground(2, notification(), types)
        } else startForeground(2, notification())
        active = true
        ProjectSession.monitor(this)
        sessionId = java.util.UUID.randomUUID().toString()
        transition(if (restored) "restored" else "starting")
        if (!managedConnection && value.durationMs != 0L) handler.post(expiry)
        try {
            manager.registerNetworkCallback(NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build(), networkCallback)
            callbackAdded = true
        } catch (_: RuntimeException) { transition("network_callback_unavailable") }
        worker = Thread({ runConnections() }, "capability-bridge").also { it.start() }
        return if (!managedConnection && value.bootCount >= 0) START_STICKY else START_NOT_STICKY
    }
    private fun transition(state: String, reason: String = "", delayMs: Long = 0) {
        if (!active && state != "stopped") return
        val row = JSONObject().put("state", state).put("reason", reason).put("attempt", attempt)
            .put("sessionId", sessionId).put("leaseRemainingMs", if (!active) 0 else if (lease?.durationMs == 0L) JSONObject.NULL else remaining())
            .put("leaseUnlimited", active && lease?.durationMs == 0L).put("leaseDurationMs", lease?.durationMs ?: duration(this)).put("retryAfterMs", delayMs)
            .put("wallMs", System.currentTimeMillis()).put("elapsedMs", SystemClock.elapsedRealtime())
            .put("screenInteractive", Probe.screen(this))
        snapshot = row
        synchronized(auditLock) {
            try {
                val file = File(filesDir, "capability-probe/bridge-events.jsonl")
                WatchDiagnostics.append(file,row.toString(),true)
            } catch (_: IOException) { /* Connection lifecycle must not depend on diagnostics. */ }
        }
        handler.post {
            if (active && (Build.VERSION.SDK_INT < 33 || checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) ==
                        android.content.pm.PackageManager.PERMISSION_GRANTED))
                getSystemService(NotificationManager::class.java).notify(2, notification())
        }
    }
    private fun closeSocket() {
        val client = socket ?: return
        val close = { try { client.close() } catch (_: IOException) {} }
        // API 27 Conscrypt sends TLS shutdown traffic even from close(). Never do it on the UI thread.
        // Capture this connection before dispatch so an old stop cannot close a replacement connection.
        if (Looper.myLooper() == Looper.getMainLooper())
            Thread(close, "capability-close").apply { isDaemon = true }.start()
        else close()
    }
    private class AuthenticationFailure : IOException()
    private fun runConnections() {
        try {
            val config = JSONObject(File(filesDir, "capability-bridge.json").readText())
            val paired = WifiConfig.parse("${config.getString("host")}:${config.getInt("port")}|${config.getString("pin")}|${config.getString("token")}")
            val endpointCache = getSharedPreferences("capability-endpoint", MODE_PRIVATE)
            var endpoint = paired
            if (endpointCache.getString("pin", "") == paired.pin) {
                val lastHost = endpointCache.getString("host", "") ?: ""
                endpoint = try { WifiConfig.parse("$lastHost:${paired.port}|${paired.pin}|${paired.token}") }
                    catch (_: IllegalArgumentException) { paired }
            }
            var needsDiscovery = false
            val trust = object : X509TrustManager {
                override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
                override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) { throw CertificateException() }
                override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                    val certificate = chain?.firstOrNull() ?: throw CertificateException()
                    certificate.checkValidity()
                    if (!MessageDigest.isEqual(MessageDigest.getInstance("SHA-256").digest(certificate.encoded), paired.pinBytes))
                        throw CertificateException("paired certificate mismatch")
                }
            }
            val tls = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), null) }
            val policy = BridgeRetryPolicy()
            while (active && remaining() > 0) {
                attempt++
                var connectedAt = 0L
                var fatal = false
                try {
                    status = "正在连接电脑（第${attempt}次）"
                    transition("connecting")
                    val network = manager.allNetworks.firstOrNull { manager.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true }
                        ?: throw IOException("no_wifi")
                    boundNetwork = network
                    if (needsDiscovery) {
                        status = "正在寻找已配对电脑"
                        transition("discovering")
                        endpoint = discover(network, paired) ?: endpoint
                    }
                    val client = tls.socketFactory.createSocket() as SSLSocket
                    socket = client; boundNetwork = network
                    if (!active || remaining() <= 0) { client.close(); break }
                    network.bindSocket(client); client.enabledProtocols = arrayOf("TLSv1.2"); client.soTimeout = 45_000
                    client.connect(InetSocketAddress(endpoint.host, endpoint.port), 5000); client.startHandshake()
                    val input = DataInputStream(client.inputStream)
                    val output = DataOutputStream(client.outputStream)
                    output.write(endpoint.tokenBytes); output.flush()
                    val acknowledgement = input.read()
                    if (acknowledgement == 0) throw AuthenticationFailure()
                    if (acknowledgement != 1) throw EOFException("connection_closed_before_authentication")
                    endpointCache.edit().putString("pin", paired.pin).putString("host", endpoint.host).apply()
                    needsDiscovery = false
                    connectedAt = SystemClock.elapsedRealtime()
                    lastPeerAt=connectedAt
                    status = "电脑已连接；等待按需请求"
                    transition("connected")
                    serve(client, input, output)
                } catch (e: Exception) {
                    // An old address may now belong to a different computer. Never relax TLS; rediscover instead.
                    fatal = e is AuthenticationFailure
                    needsDiscovery = true
                    if (active) transition("disconnected", e.javaClass.simpleName)
                } finally {
                    discoverySocket?.close(); discoverySocket = null
                    closeSocket(); socket = null; boundNetwork = null
                    val cleaned = CountDownLatch(1)
                    handler.post { if(!managedConnection&&liveService===this&&active)engine.cancelAll();cleaned.countDown() }
                    if (active && !cleaned.await(5, TimeUnit.SECONDS)) fatal = true
                }
                if (!active) break
                if (fatal) { handler.post { finish("configuration_or_cleanup_error") }; return }
                val delay = minOf(policy.disconnected(if (connectedAt > 0) SystemClock.elapsedRealtime() - connectedAt else 0), remaining())
                status = "连接暂时中断，约${delay / 1000}秒后重试"
                transition("retry_wait", delayMs = delay)
                // Network events may shorten the wait, but never below 2 seconds after a failure.
                val begin = SystemClock.elapsedRealtime()
                synchronized(retrySignal) {
                    while (active && remaining() > 0 && SystemClock.elapsedRealtime() - begin < delay) {
                        retrySignal.wait((delay - (SystemClock.elapsedRealtime() - begin)).coerceAtLeast(1))
                        if (SystemClock.elapsedRealtime() - begin >= 2000) break
                    }
                }
            }
            if (active) handler.post { finish("lease_expired") }
        } catch (_: InterruptedException) { /* Explicit stop interrupts only this worker. */ }
        catch (_: Exception) { handler.post { finish("configuration_error") } }
    }
    private fun discover(network: Network, paired: WifiConfig): WifiConfig? {
        val nonce = ByteArray(16).also { java.security.SecureRandom().nextBytes(it) }
            .joinToString("") { "%02x".format(it.toInt() and 255) }
        val data = WifiDiscovery.request(paired, nonce, "HRBRIDGEDISC1")
        val probe = MulticastSocket(null)
        discoverySocket = probe
        try {
            if (!active || remaining() <= 0) return null
            network.bindSocket(probe); probe.bind(InetSocketAddress(0))
            probe.timeToLive = 1; probe.soTimeout = 500
            val target = InetAddress.getByName(WifiDiscovery.GROUP)
            repeat(2) {
                if (!active || remaining() <= 0) return null
                probe.send(DatagramPacket(data, data.size, target, paired.port))
                val until = SystemClock.elapsedRealtime() + minOf(1000, remaining())
                while (active && remaining() > 0 && SystemClock.elapsedRealtime() < until) {
                    val packet = DatagramPacket(ByteArray(512), 512)
                    try { probe.receive(packet) } catch (_: java.net.SocketTimeoutException) { continue }
                    val found = WifiDiscovery.response(paired, nonce, packet.address.hostAddress ?: continue,
                        String(packet.data, packet.offset, packet.length, Charsets.US_ASCII), "HRBRIDGEHERE1")
                    if (found != null && found.port == paired.port) return found
                }
            }
            return null
        } finally { probe.close(); if (discoverySocket === probe) discoverySocket = null }
    }
    private fun serve(client: SSLSocket, input: DataInputStream, output: DataOutputStream) {
        var selectionId=""
        val pending = LinkedBlockingQueue<JSONObject>(16)
        reports = pending
        fun write(row: JSONObject) {
            val bytes = row.toString().toByteArray(Charsets.UTF_8)
            require(bytes.size in 1..65536)
            output.writeInt(bytes.size); output.write(bytes); output.flush()
        }
        write(state().put("id", "bridge_session").put("status", "session"))
        val sender = Thread({
            try {
                while (active && socket === client && !client.isClosed) {
                    val row = pending.poll(1, TimeUnit.SECONDS) ?: continue
                    write(row)
                }
            } catch (_: Exception) { try { client.close() } catch (_: IOException) {} }
        }, "capability-results").also { it.start() }
        try {
            while (active && remaining() > 0) {
                val size = input.readInt(); require(size in 1..8192)
                val bytes = ByteArray(size); input.readFully(bytes)
                val request = JSONObject(String(bytes, Charsets.UTF_8))
                lastPeerAt=SystemClock.elapsedRealtime()
                if (remaining() <= 0) break
                when (request.optString("op")) {
                    "select_transport" -> handler.post {
                        if(active&&socket===client){
                            val identity=request.optJSONObject("params")?.optString("selectionId")?:""
                            val result=if(managedConnection&&identity.matches(Regex("[0-9a-f]{32}")))ConnectionService.select("wifi",identity)
                                else JSONObject().put("status","invalid_selection")
                            if(result.optString("status")=="ok"){selectionId=identity;receiptSelection=identity}
                            if(!pending.offer(result.put("id",request.optString("id"))))client.close()
                        }
                    }
                    "control_state" -> if(!pending.offer(state().put("id",request.optString("id")).put("status","ok")))throw IOException("result_queue_full")
                    "sync_interaction" -> handler.post {
                        if(active && socket===client && !client.isClosed){
                            val params=request.optJSONObject("params")?:JSONObject()
                            val result=if(managedConnection && ConnectionService.owns("wifi",selectionId))AgentInteraction.sync(params,selectionId)
                                else JSONObject().put("status","transport_not_selected")
                            if(!pending.offer(result.put("id",request.optString("id"))))client.close()
                        }
                    }
                    "sync_broadcast" -> handler.post {
                        if(active&&socket===client&&!client.isClosed){
                            val result=if(managedConnection&&ConnectionService.owns("wifi",selectionId))HeartBroadcast.sync(request.optJSONObject("params")?:JSONObject(),selectionId)
                                else JSONObject().put("status","transport_not_selected")
                            if(!pending.offer(result.put("id",request.optString("id"))))client.close()
                        }
                    }
                    "sync_reminder_policy" -> handler.post {
                        if(active&&socket===client&&!client.isClosed){
                            val result=if(managedConnection&&ConnectionService.owns("wifi",selectionId))ReminderPolicy.sync(request.optJSONObject("params")?:JSONObject(),selectionId)
                                else JSONObject().put("status","transport_not_selected")
                            if(!pending.offer(result.put("id",request.optString("id"))))client.close()
                        }
                    }
                    "ping" -> if (!pending.offer(state().put("id", request.optString("id")).put("status", "pong"))) throw IOException("result_queue_full")
                    "stop_bridge" -> {
                        handler.post {
                            if(managedConnection){
                                AgentInteraction.set(this,false)
                                pending.offer(state().put("id",request.optString("id")).put("status","ok"))
                            }else finish("computer_stop")
                        }
                        if(!managedConnection)return
                    }
                    "set_standby_duration" -> handler.post {
                        if (active && socket === client && !client.isClosed) {
                            val result = applyComputerDuration(request)
                            if (!pending.offer(result)) try { client.close() } catch (_: IOException) {}
                        }
                    }
                    else -> handler.post {
                        if (active && socket === client && !client.isClosed && remaining() > 0) {
                            if(managedConnection&&request.optString("op")!="cancel"&&!ConnectionService.owns("wifi",selectionId)){
                                pending.offer(JSONObject().put("id",request.optString("id")).put("status","transport_not_selected"));return@post
                            }
                            DeviceGateway.execute(this,request) { result ->
                            // A completed old request never crosses into a new TLS connection.
                            if (active && socket === client && !client.isClosed && !pending.offer(result)) {
                                try { client.close() } catch (_: IOException) {}
                            }
                        }
                        }
                    }
                }
            }
        } finally {
            val released=CountDownLatch(1)
            handler.post{if(managedConnection)ConnectionService.release("wifi",selectionId);released.countDown()}
            if(active)released.await(3,TimeUnit.SECONDS)
            if (reports === pending) { reports = null;receiptSelection="" }; client.close(); sender.interrupt(); sender.join(1500)
        }
    }
    private fun publishState() {
        val queue = reports ?: return
        if (!queue.offer(state().put("id", "standby_state").put("status", "session"))) closeSocket()
    }
    private fun applyComputerDuration(request: JSONObject): JSONObject {
        val id = request.optString("id")
        if (!active || !followComputer) return state().put("id", id).put("status", "duration_sync_ignored")
        try {
            val params = request.getJSONObject("params")
            val revision = params.getString("revision")
            val instance = params.getString("instanceId")
            require(revision.matches(Regex("[0-9a-f]{32}|default|command-line")) && instance.matches(Regex("[0-9a-f]{32}")))
            val remainingMs = if (params.isNull("remainingMs")) null else params.getLong("remainingMs")
            val next = StandbySync.lease(params.getDouble("seconds"), remainingMs,
                SystemClock.elapsedRealtime(), System.currentTimeMillis(), bootCount(), lease,
                revision == syncRevision && instance == syncInstanceId)
            if (!getSharedPreferences("capability-standby", MODE_PRIVATE).edit()
                .putLong("started", next.startedElapsedMs).putLong("deadline", next.deadlineElapsedMs)
                .putLong("wallDeadline", next.deadlineWallMs).putLong("leaseDurationMs", next.durationMs)
                .putInt("boot", next.bootCount).putString("syncRevision", revision).putString("syncInstanceId", instance).commit())
                throw IllegalStateException("duration_save_failed")
            lease = next; syncRevision = revision; syncInstanceId = instance
            handler.removeCallbacks(expiry)
            if (managedConnection) AgentInteraction.applyRemaining(if(next.durationMs==0L)null else (next.deadlineElapsedMs-SystemClock.elapsedRealtime()).coerceAtLeast(0))
            else if (next.durationMs != 0L) handler.post(expiry)
            transition(snapshot.optString("state", "connected"), "computer_duration_synced")
            synchronized(retrySignal) { retrySignal.notifyAll() }
            return state().put("id", id).put("status", "duration_synced")
        } catch (e: Exception) {
            Probe.log(this, "standby_duration_sync_error", mapOf("error" to e.javaClass.simpleName))
            return state().put("id", id).put("status", "duration_sync_error")
        }
    }
    private fun finish(reason: String) {
        if (!managedConnection) AgentInteraction.set(this,false)
        active = false
        getSharedPreferences("capability-standby", MODE_PRIVATE).edit().putBoolean("enabled", false).commit()
        status = if (reason == "configuration_error" || reason == "configuration_or_cleanup_error") "配置或清理失败，请重新开启待命" else "接口待命已停止"
        transition("stopped", reason)
        closeSocket()
        discoverySocket?.close()
        synchronized(retrySignal) { retrySignal.notifyAll() }
        stopSelf()
    }
    override fun onDestroy() {
        ReminderEngine.unsubscribe(receiptObserver)
        // Explicit stopService is a stop too. Android process death does not call this method.
        if (active) finish("service_destroyed")
        if(!managedConnection&&liveService===this)engine.cancelAll()
        handler.removeCallbacksAndMessages(null)
        if (callbackAdded) { manager.unregisterNetworkCallback(networkCallback); callbackAdded = false }
        closeSocket(); worker?.interrupt()
        discoverySocket?.close(); discoverySocket = null
        if (liveService === this) liveService = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }
}
