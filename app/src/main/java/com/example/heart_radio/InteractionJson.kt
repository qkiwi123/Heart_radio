package com.example.heart_radio

import org.json.JSONObject

/** Strict shared wire format for independent user-controlled switches. */
object InteractionJson {
    fun wire(s:GateSnapshot):JSONObject {
        fun event(w:GateWire?):Any=w?.let {
            JSONObject().put("seq",it.event.seq).put("seen",it.event.seen).put("id",it.event.id)
                .put("enabled",it.event.enabled).put("ttlMs",it.event.ttlMs?:JSONObject.NULL)
                .put("remainingMs",it.remainingMs?:JSONObject.NULL)
        }?:JSONObject.NULL
        return JSONObject().put("pc",event(s.pc)).put("watch",event(s.watch))
    }
    fun parse(s:JSONObject):GateSnapshot {
        require(s.keys().asSequence().toSet()==setOf("pc","watch"))
        fun event(name:String):GateWire? {
            if(s.isNull(name))return null
            val e=s.getJSONObject(name)
            require(e.keys().asSequence().toSet()==setOf("seq","seen","id","enabled","ttlMs","remainingMs"))
            fun integer(key:String):Long {val n=e.get(key);require(n is Int||n is Long);return (n as Number).toLong()}
            require(e.get("enabled") is Boolean&&e.get("id") is String)
            return GateWire(GateEvent(integer("seq"),integer("seen"),e.getString("id"),e.getBoolean("enabled"),
                if(e.isNull("ttlMs"))null else integer("ttlMs")),if(e.isNull("remainingMs"))null else integer("remainingMs"))
        }
        return GateSnapshot(event("pc"),event("watch"))
    }
}
