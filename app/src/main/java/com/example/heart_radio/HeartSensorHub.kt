package com.example.heart_radio

import android.content.Context
import android.hardware.*
import android.os.Handler
import android.os.Looper

/** Main-thread owner of the only heart listener, for broadcast and bounded requests. */
object HeartSensorHub:SensorEventListener {
    private var leases:HeartLeases<SensorEventListener>?=null
    fun acquire(context:Context,id:String,listener:SensorEventListener):Boolean {
        check(Looper.myLooper()==Looper.getMainLooper())
        if(leases==null){
            val manager=Probe.manager(context.applicationContext);val sensor=Probe.sensor(context.applicationContext)
            leases=HeartLeases({sensor!=null&&manager.registerListener(this,sensor,1_000_000,0,Handler(Looper.getMainLooper()))},{manager.unregisterListener(this)})
        }
        return leases!!.acquire(id,listener)
    }
    fun release(id:String){check(Looper.myLooper()==Looper.getMainLooper());leases?.release(id)}
    fun owners()=leases?.size?:0
    override fun onSensorChanged(event:SensorEvent){leases?.each{it.onSensorChanged(event)}}
    override fun onAccuracyChanged(sensor:Sensor?,accuracy:Int){leases?.each{it.onAccuracyChanged(sensor,accuracy)}}
}
