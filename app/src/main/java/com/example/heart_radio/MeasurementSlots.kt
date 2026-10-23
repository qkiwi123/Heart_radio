package com.example.heart_radio

/** Main-thread ownership: independent sensors may overlap; location stays exclusive. */
class MeasurementSlots(private val capacity:Int=4){
    private val owners=linkedMapOf<String,String>()
    fun reserve(id:String,resource:String):Boolean{
        if(id in owners || owners.size>=capacity || resource in owners.values)return false
        if(owners.isNotEmpty() && (resource=="location" || "location" in owners.values))return false
        owners[id]=resource;return true
    }
    fun release(id:String){owners.remove(id)}
    fun entries()=owners.toMap()
}
