package com.example.heart_radio

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.*
import android.location.Location
import android.location.GnssStatus
import android.location.LocationListener
import android.location.LocationManager
import android.os.*
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Public Android APIs only. One-shot requests do not update Probe.state or its continuous heart-rate log. */
class CapabilityEngine(private val context: Context, private val handler: Handler, private val multiplex:Boolean=true) {
    private val sensors = context.getSystemService(SensorManager::class.java)
    private val visibleSensors = sensors.getSensorList(Sensor.TYPE_ALL)
    private val locations = context.getSystemService(LocationManager::class.java)
    private var activeId: String? = null
    private var cleanup: (() -> Unit)? = null
    private var finishActive: ((String) -> Unit)? = null
    private var requestWakeLock: PowerManager.WakeLock? = null
    private val log = File(context.filesDir, "capability-probe/requests.jsonl")
    private val recentIds = LinkedHashSet<String>()
    private val slots=MeasurementSlots()
    private val requests=linkedMapOf<String,CapabilityEngine>()

    fun execute(request: JSONObject, reply: (JSONObject) -> Unit) {
        val id = request.optString("id")
        if (!id.matches(Regex("[A-Za-z0-9_-]{1,64}"))) { reply(JSONObject().put("id", id).put("status", "invalid_id")); return }
        fun result(value: JSONObject) {
            value.put("id", id).put("watchResponseWallMs", System.currentTimeMillis())
                .put("watchResponseElapsedNs", SystemClock.elapsedRealtimeNanos()).put("screenInteractive", Probe.screen(context))
            if(multiplex)audit("result", value)
            reply(value)
        }
        if (!recentIds.add(id)) { result(JSONObject().put("status", "duplicate_request")); return }
        if (recentIds.size > 128) recentIds.remove(recentIds.first())
        if(multiplex)audit("request", request)
        try {
            val params = request.optJSONObject("params") ?: JSONObject()
            if(multiplex && request.optString("op") in setOf("sample","heart_sample","motion_window","locate")){
                val resource=if(request.optString("op")=="locate")"location" else {
                    val sensor=visibleSensors.getOrNull(params.getInt("sensorId"))
                    if(sensor==null){result(JSONObject().put("status","not_visible"));return}
                    "sensor:${sensor.type}"
                }
                if(!slots.reserve(id,resource)){result(JSONObject().put("status","busy"));return}
                val child=CapabilityEngine(context,handler,false)
                requests[id]=child
                try{child.execute(request){value -> requests.remove(id);slots.release(id);result(value)}}
                catch(e:Exception){requests.remove(id);slots.release(id);child.cancelAll("error");throw e}
                return
            }
            when (request.optString("op")) {
                "inventory" -> result(inventory())
                "status" -> result(deviceState())
                "cancel" -> {
                    requests.filterKeys{!params.has("targetId") || params.optString("targetId")==it}.values.toList()
                        .forEach{it.cancelAll("cancelled")}
                    if (!params.has("targetId") || params.optString("targetId") == activeId) finishActive?.invoke("cancelled")
                    result(JSONObject().put("status", "ok"))
                }
                "cached_location" -> result(cachedLocation())
                "sample" -> {
                    if (activeId != null) result(JSONObject().put("status", "busy"))
                    else sample(id, params, ::result)
                }
                "heart_sample" -> {
                    if(activeId!=null)result(JSONObject().put("status","busy"))
                    else sampleHeart(id,params,::result)
                }
                "motion_window" -> {
                    if(activeId!=null)result(JSONObject().put("status","busy"))
                    else sampleMotionWindow(id,params,::result)
                }
                "locate" -> {
                    if (activeId != null) result(JSONObject().put("status", "busy"))
                    else locate(id, params, ::result)
                }
                else -> result(JSONObject().put("status", "unknown_operation"))
            }
        } catch (e: SecurityException) { result(JSONObject().put("status", "permission_denied").put("error", e.javaClass.simpleName)) }
        catch (e: Exception) { result(JSONObject().put("status", "error").put("error", e.javaClass.simpleName).put("message", e.message)) }
    }
    // This API-27 firmware reports hardware ID 0 for every sensor. Use this session's inventory index.
    private fun descriptor(sensor: Sensor) = JSONObject().put("sensorId", visibleSensors.indexOf(sensor))
        .put("hardwareSensorId", sensor.id).put("name", sensor.name)
        .put("type", sensor.type).put("stringType", sensor.stringType).put("vendor", sensor.vendor)
        .put("reportingMode", sensor.reportingMode).put("wakeUp", sensor.isWakeUpSensor)
        .put("powerMa_declared", sensor.power.toDouble()).put("minDelayUs", sensor.minDelay)
        .put("maxDelayUs", sensor.maxDelay).put("fifoMax", sensor.fifoMaxEventCount)
        .put("resolution", sensor.resolution.toDouble()).put("maximumRange", sensor.maximumRange.toDouble())
    private fun locationPermission() = context.checkSelfPermission("android.permission.ACCESS_FINE_LOCATION") == android.content.pm.PackageManager.PERMISSION_GRANTED
    // Hold the CPU only while a bounded request runs, never while waiting for remote requests.
    private fun keepCpuForRequest(seconds: Int) {
        val lock = context.getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "HeartRadio:capability-request")
        lock.setReferenceCounted(false)
        requestWakeLock = lock
        lock.acquire((seconds + 1) * 1000L)
    }
    private fun releaseRequestWakeLock() {
        val lock = requestWakeLock
        requestWakeLock = null
        if (lock?.isHeld == true) lock.release()
    }
    private fun inventory(): JSONObject {
        val providers = locations.allProviders
        return JSONObject().put("status", "ok").put("model", Build.MODEL).put("sdk", Build.VERSION.SDK_INT)
            .put("firmware", Build.DISPLAY).put("bodySensorsPermission", Probe.allowed(context))
            .put("fineLocationPermission", locationPermission()).put("sensors", JSONArray(visibleSensors.map(::descriptor)))
            .put("sensorIdMeaning", "index in this bridge session's static inventory; not a hardware ID")
            .put("motionWindowVersion",1).put("motionWindowSeconds",30)
            .put("dynamicSensorCount", sensors.getDynamicSensorList(Sensor.TYPE_ALL).size)
            .put("locationProviders", JSONArray(providers.map { JSONObject().put("name", it).put("enabled", locations.isProviderEnabled(it)) }))
            .put("features", JSONArray(context.packageManager.systemAvailableFeatures.mapNotNull { it.name }.sorted()))
            .put("locationDiagnostic", JSONObject().put("maxSeconds", LocationWindow.MAX_SECONDS).put("gnssStatusSupported", true))
    }
    private fun deviceState(): JSONObject {
        val battery = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val manager = context.getSystemService(android.net.ConnectivityManager::class.java)
        val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        val bm = context.getSystemService(BatteryManager::class.java)
        val charge = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CHARGE_COUNTER)
        return JSONObject().put("status", "ok").put("activeRequest", activeId ?: requests.keys.firstOrNull() ?: JSONObject.NULL)
            .put("activeRequests",JSONArray(slots.entries().map{(id,resource)->JSONObject().put("requestId",id).put("resource",resource)}))
            .put("maxConcurrentMeasurements",4)
            .put("requestWakeLockHeld", requestWakeLock?.isHeld == true || requests.values.any{it.requestWakeLock?.isHeld==true})
            .put("batteryPct", if (level >= 0 && scale > 0) level * 100.0 / scale else JSONObject.NULL)
            .put("chargingStatus", battery?.getIntExtra(BatteryManager.EXTRA_STATUS, -1))
            .put("plugged", battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED, -1))
            .put("batteryTemperatureTenthsC", battery?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1))
            .put("chargeCounterUah", if (charge == Int.MIN_VALUE) JSONObject.NULL else charge)
            .put("screenInteractive", Probe.screen(context)).put("wifiAvailable", manager.allNetworks.any {
                manager.getNetworkCapabilities(it)?.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) == true })
            .put("heartRateCaptureRunning", Probe.state.running).put("heartSensorOwners",HeartSensorHub.owners())
            .put("heartSensorActive",HeartSensorHub.owners()>0).put("agentInteraction",AgentInteraction.state()).put("heartBroadcast",HeartBroadcast.state())
            .put("bridgeLifecycle", CapabilityBridgeService.state())
    }
    private fun sampleHeart(id:String,params:JSONObject,reply:(JSONObject)->Unit){
        require(params.keys().asSequence().toSet()==setOf("sensorId","seconds"))
        val duration=params.get("seconds");require(duration is Int || duration is Long)
        val maximum=(duration as Number).toLong();require(maximum in 1..15);val seconds=maximum.toInt()
        val sensorNumber=params.get("sensorId");require(sensorNumber is Int||sensorNumber is Long)
        val index=(sensorNumber as Number).toLong();require(index>=0&&index<visibleSensors.size)
        val sensor=visibleSensors.getOrNull(index.toInt())
        require(sensor!=null&&sensor.type==Sensor.TYPE_HEART_RATE&&sensor.name==Probe.sensor(context)?.name)
        if(!Probe.allowed(context)){reply(JSONObject().put("status","permission_denied"));return}
        val begin=SystemClock.elapsedRealtimeNanos();val window=HeartRequestWindow(begin,seconds);val reading=window.reading
        val owner="request:$id";val rows=JSONArray();var acquired=false
        val finish:(String)->Unit={reason->
            if(activeId==id){
                cleanup?.invoke();cleanup=null;activeId=null;finishActive=null
                val status=if(reason=="timeout")window.timeoutStatus() else reason
                reply(JSONObject().put("status",status).put("sensor",descriptor(sensor)).put("samples",rows)
                    .put("callbacks",reading.callbacks).put("requestedSeconds",seconds).put("actualElapsedMs",(SystemClock.elapsedRealtimeNanos()-begin)/1_000_000.0)
                    .put("listenersReleased",true).put("requestWakeLockHeldAtResult",requestWakeLock?.isHeld==true)
                    .put("continuousHeartPreserved",Probe.state.running).put("heartSensorOwnersAfter",HeartSensorHub.owners())
                    .put("sourceAgeVerified",false))
            }
        }
        val timeout=Runnable{finish("timeout")}
        val listener=object:SensorEventListener{
            override fun onSensorChanged(event:SensorEvent){
                if(activeId!=id)return
                val now=SystemClock.elapsedRealtimeNanos()
                if(window.expired(now)){finish("timeout");return}
                val value=event.values.firstOrNull()?:Float.NaN
                if(reading.accept(value,event.accuracy,event.timestamp,now,System.currentTimeMillis())){
                    rows.put(JSONObject().put("values",JSONArray(listOf(value.toDouble()))).put("accuracy",event.accuracy)
                        .put("eventNs",event.timestamp).put("watchReceiptWallMs",System.currentTimeMillis()))
                    finish("data_received")
                }
            }
            override fun onAccuracyChanged(sensor:Sensor?,accuracy:Int){reading.accuracyChanged(accuracy)}
        }
        activeId=id;finishActive=finish
        cleanup={if(acquired)HeartSensorHub.release(owner);reading.stop();releaseRequestWakeLock();handler.removeCallbacks(timeout)}
        try{
            keepCpuForRequest(seconds)
            acquired=HeartSensorHub.acquire(context,owner,listener);reading.registered=acquired
            if(!acquired)finish("registration_failed") else {
                val remaining=window.remainingMs(SystemClock.elapsedRealtimeNanos())
                if(remaining==0L)finish("timeout") else handler.postDelayed(timeout,remaining)
            }
        }catch(e:Exception){cleanup?.invoke();cleanup=null;activeId=null;finishActive=null;throw e}
    }
    private fun sampleMotionWindow(id:String,params:JSONObject,reply:(JSONObject)->Unit){
        require(params.keys().asSequence().toSet()==setOf("sensorId","seconds"))
        require(params.get("seconds") is Int && params.getInt("seconds")==30)
        require(params.get("sensorId") is Int)
        val sensor=visibleSensors.getOrNull(params.getInt("sensorId"))
        require(sensor!=null && sensor.type in setOf(Sensor.TYPE_ACCELEROMETER,Sensor.TYPE_GYROSCOPE))
        val begin=SystemClock.elapsedRealtimeNanos();val beginWall=System.currentTimeMillis()
        val window=MotionWindow(sensor.type,begin);var callbacks=0;var registered=false
        val finish:(String)->Unit={reason->
            if(activeId==id){
                cleanup?.invoke();cleanup=null;activeId=null;finishActive=null
                reply(JSONObject().put("status",if(reason=="window_complete" && window.count()>0)"data_received" else reason)
                    .put("sensor",descriptor(sensor)).put("summaryVersion",1).put("requestedSeconds",30)
                    .put("watchBeginElapsedNs",begin).put("watchBeginWallMs",beginWall)
                    .put("actualElapsedMs",(SystemClock.elapsedRealtimeNanos()-begin)/1_000_000.0)
                    .put("callbacks",callbacks).put("sampleCount",window.count()).put("invalidEvents",window.invalidEvents)
                    .put("sampleLimit",4096).put("truncated",window.truncated).put("rms",window.rms())
                    .put("arrivalSpanMs",window.spanMs()).put("maximumArrivalGapMs",window.maxGapMs())
                    .put("bins",JSONArray(window.bins().map{JSONObject().put("second",it.second).put("count",it.count).put("rms",it.rms)}))
                    .put("listenersReleased",true).put("requestWakeLockHeldAtResult",requestWakeLock?.isHeld==true))
            }
        }
        val timeout=Runnable{finish("window_complete")}
        val listener=object:SensorEventListener{
            override fun onSensorChanged(event:SensorEvent){
                if(activeId!=id)return
                val now=SystemClock.elapsedRealtimeNanos()
                if(now-begin>=30_000_000_000L){finish("window_complete");return}
                callbacks++
                if(!window.accept(event.values,now))finish("sample_limit")
            }
            override fun onAccuracyChanged(sensor:Sensor?,accuracy:Int){}
        }
        activeId=id;finishActive=finish
        cleanup={try{sensors.unregisterListener(listener)}finally{releaseRequestWakeLock();handler.removeCallbacks(timeout)}}
        try{
            keepCpuForRequest(30)
            registered=sensors.registerListener(listener,sensor,200_000,0,handler)
            if(!registered)finish("registration_failed") else handler.postDelayed(timeout,30_000)
        }catch(e:Exception){finish(if(e is SecurityException)"permission_denied" else "registration_error")}
    }
    private fun sample(id: String, params: JSONObject, reply: (JSONObject) -> Unit) {
        val sensorId = params.getInt("sensorId")
        val sensor = visibleSensors.getOrNull(sensorId)
        if (sensor == null) { reply(JSONObject().put("status", "not_visible")); return }
        if (sensor.type == Sensor.TYPE_HEART_RATE) { reply(JSONObject().put("status", "use_continuous_heart_cache")); return }
        val seconds = params.optInt("seconds", 5)
        require(seconds in 1..60) { "seconds must be 1..60" }
        val periodUs = params.optInt("periodUs", 200_000)
        require(periodUs in 100_000..1_000_000) { "periodUs must be 100000..1000000" }
        val rows = JSONArray()
        var callbacks = 0
        var nonFinite = 0
        var registered = false
        val beginNs = SystemClock.elapsedRealtimeNanos()
        val beginUptimeMs = SystemClock.uptimeMillis()
        val endsNs = beginNs + seconds * 1_000_000_000L
        val finish: (String) -> Unit = { reason ->
            if (activeId == id) {
                cleanup?.invoke(); cleanup = null; activeId = null; finishActive = null
                reply(JSONObject().put("status", if (reason == "window_complete" && callbacks > 0) "data_received" else reason)
                    .put("sensor", descriptor(sensor)).put("registered", registered).put("callbacks", callbacks)
                    .put("nonFiniteValues", nonFinite).put("samples", rows).put("samplesCappedAt", 64)
                    .put("requestedSeconds", seconds).put("actualElapsedMs", (SystemClock.elapsedRealtimeNanos() - beginNs) / 1_000_000.0)
                    .put("actualUptimeMs", SystemClock.uptimeMillis() - beginUptimeMs)
                    .put("requestWakeLockUsed", true).put("requestWakeLockHeldAtResult", requestWakeLock?.isHeld == true)
                    .put("listenersReleased", true).put("semanticValidation", "not_performed"))
            }
        }
        fun accept(values: FloatArray, timestamp: Long, accuracy: Int?) {
            if (SystemClock.elapsedRealtimeNanos() >= endsNs) { finish("window_complete"); return }
            callbacks++
            if (rows.length() < 64) {
                val finiteValues = JSONArray(values.map { if (it.isFinite()) it.toDouble() else { nonFinite++; JSONObject.NULL } })
                rows.put(JSONObject().put("values", finiteValues).put("accuracy", accuracy ?: JSONObject.NULL)
                    .put("eventNs", timestamp).put("arrivalNs", SystemClock.elapsedRealtimeNanos())
                    .put("watchReceiptWallMs", System.currentTimeMillis()).put("screenInteractive", Probe.screen(context)))
            }
        }
        val listener = object : SensorEventListener {
            override fun onSensorChanged(event: SensorEvent) { if (activeId == id) accept(event.values, event.timestamp, event.accuracy) }
            override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) { /* Probe.state intentionally untouched. */ }
        }
        val trigger = object : TriggerEventListener() {
            override fun onTrigger(event: TriggerEvent) { handler.post { if (activeId == id) accept(event.values, event.timestamp, null) } }
        }
        activeId = id; finishActive = finish
        val timeout = Runnable { finish("window_complete") }
        cleanup = {
            try {
                sensors.unregisterListener(listener)
                if (sensor.reportingMode == Sensor.REPORTING_MODE_ONE_SHOT) sensors.cancelTriggerSensor(trigger, sensor)
            } finally {
                releaseRequestWakeLock()
                handler.removeCallbacks(timeout)
            }
        }
        try {
            keepCpuForRequest(seconds)
            val ok = if (sensor.reportingMode == Sensor.REPORTING_MODE_ONE_SHOT) sensors.requestTriggerSensor(trigger, sensor)
                else sensors.registerListener(listener, sensor, periodUs, 0, handler)
            registered = ok
            if (!ok) { finish("registration_false"); return }
            handler.postDelayed(timeout, seconds * 1000L)
        } catch (e: Exception) {
            cleanup?.invoke(); cleanup = null; activeId = null; finishActive = null
            reply(JSONObject().put("status", if (e is SecurityException) "permission_denied" else "registration_error")
                .put("sensor", descriptor(sensor)).put("registered", false).put("error", e.javaClass.simpleName).put("listenersReleased", true))
        }
    }
    private fun locationRow(location: Location) = JSONObject().put("provider", location.provider)
        .put("latitude", location.latitude).put("longitude", location.longitude).put("fixWallMs", location.time)
        .put("fixElapsedNs", location.elapsedRealtimeNanos)
        .put("ageMs", (SystemClock.elapsedRealtimeNanos() - location.elapsedRealtimeNanos) / 1_000_000.0)
        .put("accuracyM", if (location.hasAccuracy()) location.accuracy.toDouble() else JSONObject.NULL)
        .put("mock", location.isFromMockProvider)
    private fun cachedLocation(): JSONObject {
        if (!locationPermission()) return JSONObject().put("status", "permission_denied")
        val fixes = try { locations.allProviders.mapNotNull { locations.getLastKnownLocation(it) } }
            catch (_: SecurityException) { return JSONObject().put("status", "permission_denied") }
        return JSONObject().put("status", if (fixes.isEmpty()) "no_cached_location" else "cached_data")
            .put("locations", JSONArray(fixes.map(::locationRow))).put("initiatedMeasurement", false)
    }
    private fun locate(id: String, params: JSONObject, reply: (JSONObject) -> Unit) {
        if (!locationPermission()) { reply(JSONObject().put("status", "permission_denied")); return }
        val provider = params.optString("provider", LocationManager.GPS_PROVIDER)
        if (provider !in locations.allProviders) { reply(JSONObject().put("status", "provider_absent")); return }
        if (!locations.isProviderEnabled(provider)) { reply(JSONObject().put("status", "provider_disabled")); return }
        val seconds = params.optInt("seconds", 30)
        require(seconds in 1..LocationWindow.MAX_SECONDS) { "location seconds must be 1..180" }
        val beginNs = SystemClock.elapsedRealtimeNanos()
        val beginUptimeMs = SystemClock.uptimeMillis()
        val endsNs = beginNs + seconds * 1_000_000_000L
        var fix: JSONObject? = null
        val gnss = JSONObject().put("callbackRegistered", false).put("started", false).put("stopped", false)
            .put("statusCallbacks", 0).put("maxVisible", 0).put("maxUsedInFix", 0)
            .put("firstFixMs", JSONObject.NULL).put("lastSatellites", JSONArray())
        var gnssRegistered = false
        var locationReleased = true
        var gnssReleased = true
        val cleanupErrors = JSONArray()
        val finish: (String) -> Unit = { reason ->
            if (activeId == id) {
                cleanup?.invoke(); cleanup = null; activeId = null; finishActive = null
                val released = locationReleased && gnssReleased
                reply(JSONObject().put("status", if (released) reason else "cleanup_error").put("location", fix ?: JSONObject.NULL)
                    .put("listenersReleased", released).put("locationListenerReleased", locationReleased)
                    .put("gnssCallbackReleased", gnssReleased).put("cleanupErrors", cleanupErrors)
                    .put("gnss", gnss).put("requestedSeconds", seconds).put("newFix", fix != null)
                    .put("requestBeginElapsedNs", beginNs).put("requestEndedElapsedNs", SystemClock.elapsedRealtimeNanos())
                    .put("actualElapsedMs", (SystemClock.elapsedRealtimeNanos() - beginNs) / 1_000_000.0)
                    .put("actualUptimeMs", SystemClock.uptimeMillis() - beginUptimeMs)
                    .put("requestWakeLockUsed", true).put("requestWakeLockHeldAtResult", requestWakeLock?.isHeld == true))
            }
        }
        val listener = object : LocationListener {
            override fun onLocationChanged(location: Location) {
                if (activeId != id) return
                val receivedNs = SystemClock.elapsedRealtimeNanos()
                if (receivedNs >= endsNs) { finish("timeout"); return }
                if (!LocationWindow.isFresh(location.elapsedRealtimeNanos, beginNs, receivedNs, endsNs)) return
                fix = locationRow(location); finish("data_received")
            }
            override fun onProviderEnabled(provider: String) {}
            override fun onProviderDisabled(provider: String) { finish("provider_disabled") }
            @Deprecated("Required for API 27")
            override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
        }
        val gnssCallback = object : GnssStatus.Callback() {
            override fun onStarted() { if (activeId == id) gnss.put("started", true) }
            override fun onStopped() { if (activeId == id) gnss.put("stopped", true) }
            override fun onFirstFix(ttffMillis: Int) { if (activeId == id) gnss.put("firstFixMs", ttffMillis) }
            override fun onSatelliteStatusChanged(status: GnssStatus) {
                if (activeId != id || SystemClock.elapsedRealtimeNanos() >= endsNs) return
                var used = 0
                val rows = JSONArray()
                for (index in 0 until status.satelliteCount) {
                    if (status.usedInFix(index)) used++
                    if (index < 64) rows.put(JSONObject().put("svid", status.getSvid(index))
                        .put("constellation", status.getConstellationType(index))
                        .put("cn0DbHz", status.getCn0DbHz(index).let { if (it.isFinite()) it.toDouble() else JSONObject.NULL })
                        .put("usedInFix", status.usedInFix(index)))
                }
                gnss.put("statusCallbacks", gnss.getInt("statusCallbacks") + 1)
                    .put("maxVisible", maxOf(gnss.getInt("maxVisible"), status.satelliteCount))
                    .put("maxUsedInFix", maxOf(gnss.getInt("maxUsedInFix"), used))
                    .put("lastVisible", status.satelliteCount).put("lastUsedInFix", used)
                    .put("lastStatusElapsedNs", SystemClock.elapsedRealtimeNanos()).put("lastSatellites", rows)
            }
        }
        activeId = id; finishActive = finish
        val timeout = Runnable { finish("timeout") }
        cleanup = {
            try { locations.removeUpdates(listener); locationReleased = true }
            catch (e: Exception) { cleanupErrors.put("location:${e.javaClass.simpleName}") }
            try { if (gnssRegistered) locations.unregisterGnssStatusCallback(gnssCallback); gnssReleased = true }
            catch (e: Exception) { cleanupErrors.put("gnss:${e.javaClass.simpleName}") }
            releaseRequestWakeLock(); handler.removeCallbacks(timeout)
        }
        try {
            keepCpuForRequest(seconds)
            if (provider == LocationManager.GPS_PROVIDER) {
                gnssRegistered = locations.registerGnssStatusCallback(gnssCallback, handler)
                gnssReleased = !gnssRegistered
                gnss.put("callbackRegistered", gnssRegistered)
            }
            locationReleased = false
            locations.requestLocationUpdates(provider, 0, 0f, listener, handler.looper)
            handler.postDelayed(timeout, seconds * 1000L)
        }
        catch (e: SecurityException) { cleanup?.invoke(); cleanup = null; activeId = null; finishActive = null; throw e }
        catch (e: Exception) { cleanup?.invoke(); cleanup = null; activeId = null; finishActive = null; throw e }
    }
    fun cancelAll(reason:String="disconnected") {
        requests.values.toList().forEach{it.cancelAll(reason)}
        finishActive?.invoke(reason)
    }
    private fun audit(kind: String, value: JSONObject) {
        WatchDiagnostics.append(log,JSONObject().put("kind",kind).put("wallMs",System.currentTimeMillis()).put("data",value).toString(),true)
    }
}
