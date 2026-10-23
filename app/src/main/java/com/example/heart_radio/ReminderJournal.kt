package com.example.heart_radio

/** Keep unexpired IDs even when full. A reused ID never starts another effect. */
class ReminderJournal<T>(private val capacity:Int=128) {
    data class Entry<T>(val id:String,val fingerprint:String?,val expiresAtMs:Long,val value:T)
    private val rows=linkedMapOf<String,Entry<T>>()
    fun get(id:String)=rows[id]
    fun entries()=rows.values.toList()
    fun restore(entry:Entry<T>){require(rows.size<capacity || rows.containsKey(entry.id));rows[entry.id]=entry}
    fun reserve(entry:Entry<T>,now:Long):String {
        rows[entry.id]?.let { return if(it.fingerprint==null || it.fingerprint==entry.fingerprint)"duplicate" else "reminder_id_conflict" }
        if(rows.size>=capacity){
            val expired=rows.values.firstOrNull{it.expiresAtMs<=now}?:return "reminder_ledger_full"
            rows.remove(expired.id)
        }
        rows[entry.id]=entry;return "reserved"
    }
    fun remove(id:String){rows.remove(id)}
    /** Erase content, keep identity/expiry for deduplication, and roll back on storage failure. */
    fun redact(ids:Set<String>, replacement:(Entry<T>)->T, commit:()->Unit):List<Entry<T>> {
        val original=rows.values.filter{it.id in ids}
        if(original.isEmpty())return emptyList()
        val changed=original.map{it.copy(value=replacement(it))}
        changed.forEach{rows[it.id]=it}
        try{commit()}catch(e:Exception){original.forEach{rows[it.id]=it};throw e}
        return changed
    }
}
