package com.example.heart_radio

import android.annotation.SuppressLint
import android.app.*
import android.bluetooth.*
import android.content.*
import android.hardware.*
import android.os.*
import org.json.JSONObject
import java.io.*
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** Isolated, paired RFCOMM experiment. No Wi-Fi changes, persistent CPU lock, or automatic HR start. */
@SuppressLint("MissingPermission")
class RfcommProbeService : Service() {
    companion object {
        val SERVICE_UUID: UUID = UUID.fromString("749b3000-6f67-4d32-96dd-4d778c27a101")
        private const val MAX_FRAME=16384
        @Volatile var status="蓝牙双向测试未启动"
        @Volatile private var live: RfcommProbeService?=null
        fun running()=live?.active==true
        fun connected()=live?.let{service->service.link?.let{it.authenticated&&(!service.managedConnection||SystemClock.elapsedRealtime()-it.lastReceivedMs<45_000)}==true}==true
        fun heartSummary()=live?.heartUiText()?:"心率未运行"
        fun cancelEffects() { live?.cancelComparison() }
        fun compareVibration() { live?.let { it.handler.post { it.testVibration() } } }
    }
    private data class Link(val socket:BluetoothSocket, val outgoing:LinkedBlockingQueue<ByteArray> = LinkedBlockingQueue(128)) {
        @Volatile var authenticated=false
        @Volatile var lastReceivedMs=SystemClock.elapsedRealtime()
        var selectionId=""
    }
    private val handler=Handler(Looper.getMainLooper())
    private val reading=Probe.state
    private val heartDiagnostics=HeartStream.diagnostics
    private var managedConnection=false
    private val heartObserver:(Int)->Unit={ kind ->
        if(kind==1) HeartStream.lastEvent?.let { row ->
            audit("heart_event",row)
            link?.takeIf{it.authenticated&&(!managedConnection||ConnectionService.owns("rfcomm",it.selectionId))}?.let{
                send(it,JSONObject(row.toString()).put("selectionId",it.selectionId))
            }
        }
        emitHeartState()
    }
    private var lastHeartStateKey=""
    private val receiptObserver:(JSONObject)->Unit={ row ->
        link?.takeIf{it.authenticated && (!managedConnection || ConnectionService.owns("rfcomm",it.selectionId))}?.let {
            send(it,JSONObject(row.toString()).put("selectionId",it.selectionId))
        }
    }
    @Volatile private var active=false
    @Volatile private var server:BluetoothServerSocket?=null
    @Volatile private var link:Link?=null
    private var worker:Thread?=null
    private var allowedPc=""
    private var token=""
    private var endsNs=0L
    private var connectionCount=0
    private var eventSequence=0
    private var vibrationTestEndsNs=0L
    private lateinit var auditFile:File
    private val ticker=object:Runnable { override fun run() {
        if(!active)return
        if(SystemClock.elapsedRealtimeNanos()>=endsNs){finish("test_deadline");return}
        emitHeartState()
        link?.takeIf { it.authenticated }?.let { send(it, state().put("event","heartbeat")) }
        handler.postDelayed(this,5000)
    } }
    override fun onBind(intent:Intent?)=null
    override fun onCreate(){
        super.onCreate(); live=this
        HeartStream.subscribe(heartObserver)
        auditFile=File(filesDir,"rfcomm-probe/events.jsonl")
        ReminderEngine.subscribe(receiptObserver)
    }
    private fun audit(kind:String, fields:JSONObject=JSONObject()) {
        val row=JSONObject(fields.toString()).put("kind",kind).put("wallMs",System.currentTimeMillis())
            .put("elapsedNs",SystemClock.elapsedRealtimeNanos()).put("screenInteractive",Probe.screen(this))
        WatchDiagnostics.append(auditFile,row.toString(),kind!="heart_event")
    }
    override fun onStartCommand(intent:Intent?,flags:Int,startId:Int):Int {
        if(ProjectSession.suspended){stopSelf(startId);return START_NOT_STICKY}
        if(intent?.action=="STOP"){finish("user_stop");return START_NOT_STICKY}
        if(active)return START_NOT_STICKY
        managedConnection=intent?.getBooleanExtra("managed_connection",false)==true
        try {
            require(Probe.bluetoothAllowed(this)){"蓝牙权限未授权"}
            val config=JSONObject(File(filesDir,"rfcomm-probe.json").readText())
            allowedPc=config.getString("allowPc").uppercase(java.util.Locale.US)
            token=config.getString("token")
            require(BluetoothAdapter.checkBluetoothAddress(allowedPc) && token.matches(Regex("[0-9a-f]{64}"))){"测试配置无效"}
            val adapter=getSystemService(BluetoothManager::class.java).adapter
            require(adapter?.isEnabled==true){"蓝牙未开启"}
            require(adapter.getRemoteDevice(allowedPc).bondState==BluetoothDevice.BOND_BONDED){"电脑尚未配对"}
            val nm=getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel("rfcomm_probe","蓝牙双向测试",NotificationManager.IMPORTANCE_LOW))
            val stop=PendingIntent.getService(this,31,Intent(this,if(managedConnection)ConnectionService::class.java else RfcommProbeService::class.java).setAction("STOP"),PendingIntent.FLAG_IMMUTABLE)
            val open=PendingIntent.getActivity(this,32,Intent(this,RfcommProbeActivity::class.java),PendingIntent.FLAG_IMMUTABLE)
            val notification=Notification.Builder(this,"rfcomm_probe").setSmallIcon(android.R.drawable.ic_menu_info_details)
                .setContentTitle(if(managedConnection)"蓝牙连接" else "蓝牙双向测试中").setContentText(if(managedConnection)"心率与Agent交互独立控制" else "最长60分钟，可随时停止").setContentIntent(open)
                .addAction(Notification.Action.Builder(null,"停止测试",stop).build()).build()
            if(Build.VERSION.SDK_INT>=34) startForeground(3,notification,android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
            else startForeground(3,notification)
            server=adapter.listenUsingRfcommWithServiceRecord("HeartRadio probe",SERVICE_UUID)
            active=true;endsNs=if(managedConnection)Long.MAX_VALUE else SystemClock.elapsedRealtimeNanos()+3_600_000_000_000L
            ProjectSession.monitor(this)
            status="等待已配对电脑连接";audit("server_start",JSONObject().put("uuid",SERVICE_UUID).put("maximumSeconds",3600))
            worker=Thread({acceptConnections()},"rfcomm-probe-reader").also { it.start() }
            handler.post(ticker)
        } catch(e:Exception) { status="蓝牙测试失败：${e.message}";audit("start_failed",JSONObject().put("error",e.toString()));finish("start_failed",false) }
        return START_NOT_STICKY
    }
    private fun acceptConnections(){
        while(active){
            var current:Link?=null
            try {
                val client=server?.accept(1000)?:break
                if(!client.remoteDevice.address.equals(allowedPc,true)||client.remoteDevice.bondState!=BluetoothDevice.BOND_BONDED){
                    client.close();audit("rejected_peer");continue
                }
                current=Link(client);link=current
                val connected=current
                val authTimeout=Runnable { if(link===connected&&!connected.authenticated)try{connected.socket.close()}catch(_:IOException){} }
                handler.postDelayed(authTimeout,10_000)
                val writer=Thread({writeLoop(connected)},"rfcomm-probe-writer").also { it.start() }
                val input=DataInputStream(client.inputStream)
                while(active&&link===connected){
                    val size=input.readInt();require(size in 1..MAX_FRAME){"frame too large"}
                    val bytes=ByteArray(size);input.readFully(bytes)
                    val request=JSONObject(String(bytes,Charsets.UTF_8))
                    connected.lastReceivedMs=SystemClock.elapsedRealtime()
                    if(!connected.authenticated){
                        require(request.optString("op")=="hello"&&request.optInt("version")==1&&
                            MessageDigest.isEqual(request.optString("token").toByteArray(),token.toByteArray())){"authentication failed"}
                        connected.authenticated=true;connectionCount++;handler.removeCallbacks(authTimeout)
                        handler.post {
                            status="蓝牙双向已连接"
                            audit("connected",JSONObject().put("connection",connectionCount))
                            send(connected,state().put("event","hello").put("protocolVersion",1))
                        }
                    }else handler.post { if(active&&link===connected) execute(connected,request) }
                }
                writer.interrupt()
            }catch(e:IOException){
                if(active&&current!=null)audit("disconnected",JSONObject().put("error",e.javaClass.simpleName))
                if(active&&getSystemService(BluetoothManager::class.java).adapter?.isEnabled!=true){handler.post{if(live===this&&active)finish("radio_unavailable")};break}
            }
            catch(e:Exception){audit("link_error",JSONObject().put("error",e.javaClass.simpleName))}
            finally{
                val old=current
                if(old!=null){ if(link===old)link=null;try{old.socket.close()}catch(_:IOException){} }
                if(old!=null)handler.post {
                    if(managedConnection)ConnectionService.release("rfcomm",old.selectionId)
                    else if(live===this&&active&&link==null)DeviceGateway.cancelAll()
                    if(live===this&&active&&link==null)status="连接断开，等待电脑重连"
                }
            }
        }
    }
    private fun writeLoop(current:Link){
        try{
            val output=DataOutputStream(current.socket.outputStream)
            while(active&&link===current){
                val bytes=current.outgoing.poll(1,TimeUnit.SECONDS)?:continue
                output.writeInt(bytes.size);output.write(bytes);output.flush()
            }
        }catch(_:Exception){ try{current.socket.close()}catch(_:IOException){} }
    }
    private fun send(current:Link,value:JSONObject){
        val bytes=JSONObject(value.toString()).put("watchResponseWallMs",System.currentTimeMillis()).toString().toByteArray(Charsets.UTF_8)
        if(bytes.size>MAX_FRAME||!current.outgoing.offer(bytes)){
            audit("outbound_overflow");try{current.socket.close()}catch(_:IOException){}
        }
    }
    private fun state():JSONObject{
        val now=SystemClock.elapsedRealtimeNanos()
        val heart=heartDiagnosticJson(now)
        return JSONObject().put("status","ok").put("transport","rfcomm").put("connected",link?.authenticated==true)
            .put("agentInteraction",AgentInteraction.state()).put("heartBroadcast",HeartBroadcast.state()).put("captureOwner","CaptureService")
            .put("managedConnection",managedConnection)
            .put("connectionControl",ConnectionService.state())
            .put("heartRunning",reading.running).put("heartRegistered",reading.registered)
            .put("heartCallbacks",reading.callbacks).put("heartValidCount",reading.validCount)
            .put("bpm",if(managedConnection)JSONObject.NULL else reading.recentValue(now)?:JSONObject.NULL).put("sourceAgeVerified",false)
            .put("heartState",heart.getString("state")).put("heartStateMessage",heart.getString("message"))
            .put("heartDiagnostic",heart)
            .put("testRemainingSeconds",if(managedConnection)JSONObject.NULL else ((endsNs-now)/1_000_000_000L).coerceAtLeast(0))
            .put("connectionUnlimited",managedConnection)
    }
    private fun heartDiagnosticJson(now:Long):JSONObject = HeartStream.diagnosticJson()
    private fun emitHeartState() {
        val view=heartDiagnostics.snapshot(reading,SystemClock.elapsedRealtimeNanos())
        val key="${view.attemptId}:${view.state}:${view.stopReason}:${view.failureReason}"
        if(key==lastHeartStateKey)return
        lastHeartStateKey=key
        val packet=state().put("event","heart_state")
        audit("heart_state",packet)
        link?.takeIf{it.authenticated}?.let{send(it,packet)}
    }
    private fun heartUiText():String {
        val now=SystemClock.elapsedRealtimeNanos()
        val view=heartDiagnostics.snapshot(reading,now)
        val value=reading.recentValue(now)
        val detail=when(view.state) {
            "waiting_first_data","first_data_timeout" -> "已等待${(view.elapsedMs?:0)/1000}秒"
            "ready" -> "${value?.toInt()} 次/分 · 接收${(view.lastCallbackAgeMs?:0)/1000}秒前"
            "stopped" -> "本次回调${reading.callbacks}次，有效${reading.validCount}次"
            else -> "回调${reading.callbacks}次，有效${reading.validCount}次"
        }
        return "${view.message}\n$detail"
    }
    private fun execute(current:Link,request:JSONObject){
        val id=request.optString("id")
        if(!id.matches(Regex("[A-Za-z0-9_-]{1,64}"))){send(current,JSONObject().put("status","invalid_id"));return}
        val params=request.optJSONObject("params")?:JSONObject()
        val op=request.optString("op")
        if(op=="select_transport"){
            val identity=params.optString("selectionId")
            if(!managedConnection||!identity.matches(Regex("[0-9a-f]{32}"))){send(current,JSONObject().put("id",id).put("status","invalid_selection"));return}
            val result=ConnectionService.select("rfcomm",identity)
            if(result.optString("status")=="ok")current.selectionId=identity
            send(current,result.put("id",id).put("op",op));return
        }
        if(managedConnection&&op !in setOf("ping","control_state","stop","stop_heart","stop_bridge","cancel")&&!ConnectionService.owns("rfcomm",current.selectionId)){
            send(current,JSONObject().put("id",id).put("status","transport_not_selected"));return
        }
        if(op in setOf("inventory","status","sample","heart_sample","motion_window","cancel","cached_location","locate","remind","reminder_status")) {
            DeviceGateway.execute(this,request) { raw ->
                if(op=="status"&&raw.optString("status")=="ok") {
                    val control=state();control.keys().forEach { key -> if(!raw.has(key))raw.put(key,control.get(key)) }
                }
                raw.put("id",id).put("op",op);audit("command_result",raw);send(current,raw)
            }
            return
        }
        if(op=="sync_interaction"){
            val result=if(managedConnection)AgentInteraction.sync(params,current.selectionId) else JSONObject().put("status","managed_connection_required")
            send(current,result.put("id",id).put("op",op));return
        }
        if(op=="sync_broadcast"){
            val result=if(managedConnection&&ConnectionService.owns("rfcomm",current.selectionId))HeartBroadcast.sync(params,current.selectionId)
                else JSONObject().put("status","managed_connection_required")
            send(current,result.put("id",id).put("op",op));return
        }
        if(op=="sync_reminder_policy"){
            val result=if(managedConnection&&ConnectionService.owns("rfcomm",current.selectionId))ReminderPolicy.sync(params,current.selectionId)
                else JSONObject().put("status","managed_connection_required")
            send(current,result.put("id",id).put("op",op));return
        }
        if(op in setOf("start_heart","stop_heart")) {
            if(managedConnection&&op=="start_heart") { send(current,JSONObject().put("id",id).put("status","local_heart_control_required"));return }
            if(op=="start_heart")startForegroundService(Intent(this,CaptureService::class.java).setAction("HEART_START"))
            else stopService(Intent(this,CaptureService::class.java))
            handler.postDelayed({if(link===current)send(current,state().put("id",id).put("op",op))},150)
            return
        }
        if(op in setOf("remind","test_vibration")&&!AgentInteraction.enabled()) {
            send(current,JSONObject().put("id",id).put("status","interaction_disabled"));return
        }
        val result=try{
            when(op){
                "ping" -> state().put("echo",params.optString("echo").take(256))
                "control_state" -> state()
                "stop_bridge" -> {AgentInteraction.set(this,false);state()}

                "test_vibration" -> testVibration()
                "stop" -> {handler.postDelayed({if(managedConnection)ConnectionService.stop(this) else finish("computer_stop")},500);JSONObject().put("status","stopping")}
                else -> JSONObject().put("status","unknown_operation")
            }
        }catch(e:Exception){JSONObject().put("status","invalid_request").put("message",e.message)}
        result.put("id",id).put("op",op);audit("command_result",result);send(current,result)
    }
    private fun testVibration():JSONObject{
        if(!AgentInteraction.enabled())return JSONObject().put("status","interaction_disabled")
        val now=SystemClock.elapsedRealtimeNanos()
        if(now<vibrationTestEndsNs)return JSONObject().put("status","busy")
        val system=ReminderSystemState.read(this)
        if(!system.decision.effectsAllowed)
            return JSONObject().put("status",if(system.decision.suppressedByDnd)"suppressed_by_dnd" else "suppressed_by_system")
                .put("systemAlertPolicy",system.json)
        val vibrator=getSystemService(Vibrator::class.java)
        if(!vibrator.hasVibrator())return JSONObject().put("status","no_vibrator")
        if(!vibrator.hasAmplitudeControl())return JSONObject().put("status","amplitude_control_unavailable")
        val timings=ReminderVibration.comparisonTimings()
        val amplitudes=ReminderVibration.comparisonAmplitudes()
        vibrator.vibrate(VibrationEffect.createWaveform(timings,amplitudes,-1),
            ReminderSystemState.vibrationAttributes(system))
        vibrationTestEndsNs=now+timings.sum()*1_000_000
        val result=JSONObject().put("status","issued").put("systemAlertPolicy",system.json).put("order",org.json.JSONArray(listOf("light","standard","strong")))
            .put("timingsMs",org.json.JSONArray(timings.toList())).put("amplitudes",org.json.JSONArray(amplitudes.toList()))
            .put("totalMs",timings.sum()).put("sameLevelGapMs",500).put("betweenLevelsGapMs",2000)
        audit("vibration_comparison",result)
        handler.postDelayed({
            audit("vibration_comparison_timer_complete")
            link?.takeIf{it.authenticated}?.let{send(it,JSONObject().put("event","vibration_comparison_timer_complete"))}
        },timings.sum())
        return result
    }
    private fun finish(reason:String,updateStatus:Boolean=true){
        if(updateStatus)status="蓝牙双向测试已停止"
        active=false
        if(managedConnection)link?.let{ConnectionService.release("rfcomm",it.selectionId)} else DeviceGateway.cancelAll()
        cancelComparison();handler.removeCallbacksAndMessages(null)
        try{link?.socket?.close()}catch(_:IOException){};link=null
        try{server?.close()}catch(_:IOException){};server=null
        audit("server_stop",JSONObject().put("reason",reason));stopForeground(STOP_FOREGROUND_REMOVE);stopSelf()
    }
    override fun onDestroy(){
        active=false;HeartStream.unsubscribe(heartObserver);ReminderEngine.unsubscribe(receiptObserver)
        if(managedConnection)link?.let{ConnectionService.release("rfcomm",it.selectionId)} else if(live===this)DeviceGateway.cancelAll()
        cancelComparison();handler.removeCallbacksAndMessages(null)
        if(!status.startsWith("蓝牙测试失败"))status="蓝牙双向测试已停止"
        try{link?.socket?.close()}catch(_:IOException){};link=null
        try{server?.close()}catch(_:IOException){};server=null
        audit("service_destroy");if(live===this)live=null;super.onDestroy()
    }
    private fun cancelComparison(){
        if(vibrationTestEndsNs>SystemClock.elapsedRealtimeNanos())getSystemService(Vibrator::class.java).cancel()
        vibrationTestEndsNs=0
    }
}
