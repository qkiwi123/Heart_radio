package com.example.heart_radio

import java.util.UUID

/** Two-author causal register. Network merges never invent an enabling edit. */
data class GateEvent(val seq: Long, val seen: Long, val id: String, val enabled: Boolean, val ttlMs: Long?)
data class GateWire(val event: GateEvent, val remainingMs: Long?)
data class GateSnapshot(val pc: GateWire?, val watch: GateWire?)

class InteractionSync(private val author: String, private val now: () -> Long) {
    companion object { const val MAX_MS = 31_536_000_000L; const val MAX_SEQ = 2_147_483_647L }
    var pc: GateEvent? = null; private set
    var watch: GateEvent? = null; private set
    val deadlines = mutableMapOf<String,Long>()
    init { require(author == "pc" || author == "watch") }
    private fun active(): List<GateEvent> {
        val p=pc; val w=watch
        return when { p==null -> listOfNotNull(w); w==null -> listOf(p)
            p.seen>=w.seq -> listOf(p); w.seen>=p.seq -> listOf(w); else -> listOf(p,w) }
    }
    val enabled: Boolean get() = active().let { it.isNotEmpty() && it.all { e -> e.enabled } }
    fun remaining(): Long? = if(!enabled)null else active().mapNotNull { it.ttlMs?.let { _ -> deadlines.getValue(it.id) } }.minOrNull()?.let { (it-now()).coerceAtLeast(0) }
    fun fingerprint() = "${pc?.seq?:0}-${pc?.id?:""}:${watch?.seq?:0}-${watch?.id?:""}"
    fun edit(value: Boolean, ttlMs: Long? = null, force: Boolean = false): Boolean {
        require(ttlMs==null || ttlMs in 1..MAX_MS)
        if(value && enabled && !force)return false
        val own=if(author=="pc")pc else watch; val peer=if(author=="pc")watch else pc
        val seq=(own?.seq?:0)+1; require(seq<=MAX_SEQ)
        val event=GateEvent(seq,peer?.seq?:0,UUID.randomUUID().toString().replace("-",""),value,if(value)ttlMs else null)
        if(author=="pc")pc=event else watch=event
        event.ttlMs?.let { deadlines[event.id]=now()+it };prune();return true
    }
    fun expire(): Boolean { if(enabled && remaining()==0L){edit(false);return true};return false }
    fun snapshot() = GateSnapshot(wire(pc),wire(watch))
    private fun wire(e: GateEvent?) = e?.let { GateWire(it,it.ttlMs?.let { _ -> (deadlines.getValue(it.id)-now()).coerceAtLeast(0) }) }
    fun copy(): InteractionSync = InteractionSync(author,now).also { it.pc=pc;it.watch=watch;it.deadlines.putAll(deadlines) }
    private fun validate(s: GateSnapshot) {
        listOfNotNull(s.pc,s.watch).forEach { (e,left) ->
            require(e.seq in 1..MAX_SEQ && e.seen in 0..MAX_SEQ && e.id.matches(Regex("[0-9a-f]{32}")))
            require(if(e.ttlMs==null)left==null else e.enabled && e.ttlMs in 1..MAX_MS && left!=null && left in 0..e.ttlMs)
        }
        val p=s.pc?.event;val w=s.watch?.event
        require(p==null || p.seen<=(w?.seq?:0));require(w==null || w.seen<=(p?.seq?:0))
        require(p==null || w==null || p.seen<w.seq || w.seen<p.seq)
    }
    fun merge(s: GateSnapshot) {
        validate(s)
        fun next(old: GateEvent?, incoming: GateWire?): GateEvent? {
            val e=incoming?.event?:return old
            if(old==null || e.seq>old.seq)return e
            if(e.seq==old.seq)require(e==old){"Conflicting event identity"}
            return old
        }
        val p=next(pc,s.pc);val w=next(watch,s.watch)
        validate(GateSnapshot(p?.let { GateWire(it,it.ttlMs) },w?.let { GateWire(it,it.ttlMs) }))
        pc=p;watch=w
        listOfNotNull(s.pc,s.watch).forEach { (e,left) ->
            if(e.ttlMs!=null && (pc?.id==e.id || watch?.id==e.id)){
                val end=now()+requireNotNull(left);deadlines[e.id]=minOf(deadlines[e.id]?:end,end)
            }
        }
        prune();expire()
    }
    private fun prune(){val ids=listOfNotNull(pc?.id,watch?.id);deadlines.keys.retainAll(ids)}
}
