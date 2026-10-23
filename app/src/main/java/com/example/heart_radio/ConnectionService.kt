package com.example.heart_radio

import android.app.*
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONObject

/** Transport readiness is independent of capture and permission. Never changes system radios. */
class ConnectionService : Service() {
    companion object {
        @Volatile private var live:ConnectionService?=null
        @Volatile var status="连接未开启";private set
        fun running()=live?.active==true
        fun mode(context:Context)=context.getSharedPreferences("connection-control",MODE_PRIVATE).getString("mode","auto_bluetooth")?:"auto_bluetooth"
        fun resume(context:Context){if(!ProjectSession.suspended&&context.getSharedPreferences("connection-control",MODE_PRIVATE).getBoolean("enabled",true)&&!running())start(context)}
        fun start(context:Context,mode:String=mode(context)){
            require(mode in TransportSelection.modes)
            if(!ProjectSession.resume(context)){status="恢复失败：暂停状态无法保存";return}
            context.startForegroundService(Intent(context,ConnectionService::class.java).putExtra("mode",mode))
        }
        fun timeoutStop(context:Context){
            status="未连接满10分钟，项目已停止；开启连接恢复原设置"
            context.stopService(Intent(context,ConnectionService::class.java))
        }
        fun stop(context:Context){
            context.getSharedPreferences("connection-control",MODE_PRIVATE).edit().putBoolean("enabled",false).apply()
            context.stopService(Intent(context,ConnectionService::class.java))
        }
        fun connected():Boolean=live?.let{when(it.route.transport){
            "rfcomm"->RfcommProbeService.connected();"wifi"->CapabilityBridgeService.connected();else->false
        }}?:false
        fun currentTransport()=live?.route?.transport
        fun canPublish(transport:String)=live?.let{it.active&&it.route.transport==transport}?:false
        fun selectionId()=live?.route?.session
        fun owns(transport:String,session:String)=live?.route?.owns(transport,session)==true
        fun select(transport:String,session:String):JSONObject{
            val owner=live?:return JSONObject().put("status","connection_disabled")
            if(!owner.active||transport !in TransportSelection.allowed(owner.selected))return JSONObject().put("status","transport_not_allowed")
            if(owner.route.owns(transport,session))return owner.report().put("status","ok")
            if(!owner.route.owns(transport,session))DeviceGateway.cancelAll()
            owner.route.select(owner.selected,transport,session)
            if(transport=="wifi")owner.wifi?.beginSelection() else owner.wifi?.discardPending()
            Probe.log(owner,"transport_selected",mapOf("transport" to transport,"selectionId" to session))
            return owner.report().put("status","ok")
        }
        fun release(transport:String,session:String){
            val owner=live?:return
            if(owner.route.release(transport,session)){DeviceGateway.cancelAll();owner.wifi?.discardPending()}
        }
        fun state():JSONObject=(live?.report()?:JSONObject().put("running",false).put("selectedTransport",JSONObject.NULL)).put("projectSession",ProjectSession.state())
    }
    private var active=false
    private var selected=""
    private val route=TransportSelection()
    private var wifi:WifiTransport?=null
    private var bluetoothOwned=false
    private var wifiOwned=false
    private var nextBluetooth=0L
    private var nextWifi=0L
    private val handler=Handler(Looper.getMainLooper())
    private val heartObserver:(Int)->Unit={kind->if(kind!=1||canPublish("wifi"))wifi?.publish(kind)}
    private fun report()=JSONObject().put("running",active).put("mode",selected)
        .put("selectedTransport",route.transport?:JSONObject.NULL).put("selectionId",route.session?:JSONObject.NULL)
        .put("automaticRouting",true).put("wifiHeartConnected",wifi?.isConnected()==true)
        .put("wifiHeartSession",wifi?.sessionId()?:JSONObject.NULL)
    private fun ensureAdapters(){
        val now=SystemClock.elapsedRealtime();val allowed=TransportSelection.allowed(selected)
        if("rfcomm" in allowed&&!RfcommProbeService.running()&&now>=nextBluetooth){
            nextBluetooth=now+10_000
            if(java.io.File(filesDir,"rfcomm-probe.json").isFile){
                bluetoothOwned=true
                startForegroundService(Intent(this,RfcommProbeService::class.java).putExtra("managed_connection",true))
            }
        }
        if("wifi" in allowed&&now>=nextWifi){
            nextWifi=now+10_000
            if(wifi==null)try{
                val code=getSharedPreferences("wifi",MODE_PRIVATE).getString("connection_code","")?:""
                wifi=WifiTransport(this,WifiConfig.parse(code),{canPublish("wifi")}).also{it.start()}
            }catch(_:Exception){Probe.wifiStatus="Wi-Fi未配置，等待连接设置"}
            if(!CapabilityBridgeService.isRunning()&&java.io.File(filesDir,"capability-bridge.json").isFile){
                wifiOwned=true
                startForegroundService(Intent(this,CapabilityBridgeService::class.java).putExtra("managed_connection",true))
            }
        }
    }
    private fun closeAdapters(){
        route.session?.let{route.release(route.transport!!,it)};DeviceGateway.cancelAll()
        wifi?.close();wifi=null
        if(bluetoothOwned)stopService(Intent(this,RfcommProbeService::class.java))
        if(wifiOwned)stopService(Intent(this,CapabilityBridgeService::class.java))
        bluetoothOwned=false;wifiOwned=false
    }
    private val refresh=object:Runnable{override fun run(){
        if(!active)return
        ensureAdapters()
        status=if(connected())"${if(route.transport=="rfcomm")"蓝牙" else "Wi-Fi"}已连接电脑" else "等待电脑 · ${TransportSelection.label(selected)}"
        AgentInteraction.enabled();handler.postDelayed(this,1000)
    }}
    override fun onBind(intent:Intent?)=null
    override fun onCreate(){super.onCreate();live=this;HeartStream.subscribe(heartObserver)}
    override fun onStartCommand(intent:Intent?,flags:Int,startId:Int):Int{
        if(ProjectSession.suspended){stopSelf(startId);return START_NOT_STICKY}
        if(intent?.action=="STOP"){stop(this);return START_NOT_STICKY}
        val requested=intent?.getStringExtra("mode")?:mode(this)
        if(active&&requested==selected)return START_NOT_STICKY
        try{
            require(requested in TransportSelection.modes)
            if(active)closeAdapters()
            val nm=getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel("connection","电脑连接",NotificationManager.IMPORTANCE_LOW))
            val stop=PendingIntent.getService(this,51,Intent(this,ConnectionService::class.java).setAction("STOP"),PendingIntent.FLAG_IMMUTABLE)
            val open=PendingIntent.getActivity(this,52,Intent(this,MainActivity::class.java),PendingIntent.FLAG_IMMUTABLE)
            val notification=Notification.Builder(this,"connection").setSmallIcon(android.R.drawable.ic_menu_info_details)
                .setContentTitle("电脑连接").setContentText("${TransportSelection.label(requested)} · 心率及交互独立")
                .setContentIntent(open).addAction(Notification.Action.Builder(null,"断开连接",stop).build()).build()
            if(Build.VERSION.SDK_INT>=34)startForeground(5,notification,android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
            else startForeground(5,notification)
            selected=requested;active=true;nextBluetooth=0;nextWifi=0
            ProjectSession.monitor(this)
            getSharedPreferences("connection-control",MODE_PRIVATE).edit().putString("mode",selected).putBoolean("enabled",true).apply()
            handler.removeCallbacks(refresh);handler.post(refresh)
            Probe.log(this,"connection_control_start",mapOf("mode" to selected,"automaticRouting" to true))
        }catch(e:Exception){status="连接启动失败：${e.message}";stopSelf()}
        return START_NOT_STICKY
    }
    override fun onDestroy(){
        active=false;handler.removeCallbacksAndMessages(null);HeartStream.unsubscribe(heartObserver);closeAdapters()
        if(!status.startsWith("连接启动失败")&&!ProjectSession.suspended)status="连接已断开"
        Probe.log(this,"connection_control_stop",mapOf("heartContinues" to Probe.state.running,"interactionEnabled" to AgentInteraction.enabled()))
        if(live===this)live=null
        stopForeground(STOP_FOREGROUND_REMOVE);super.onDestroy()
    }
}
