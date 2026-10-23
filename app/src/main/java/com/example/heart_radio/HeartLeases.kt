package com.example.heart_radio

/** One physical registration shared by independent owners. Failed acquisition owns nothing. */
class HeartLeases<T>(private val register:()->Boolean,private val unregister:()->Unit) {
    private val owners=linkedMapOf<String,T>()
    val size get()=owners.size
    fun acquire(id:String,value:T):Boolean {
        if(id in owners)return owners[id]===value
        if(owners.isEmpty()&&!register())return false
        owners[id]=value;return true
    }
    fun release(id:String){if(owners.remove(id)!=null&&owners.isEmpty())unregister()}
    fun each(action:(T)->Unit){owners.toList().forEach{(id,value)->if(owners[id]===value)action(value)}}
}
