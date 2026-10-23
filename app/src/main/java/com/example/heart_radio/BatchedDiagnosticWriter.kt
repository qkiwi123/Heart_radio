package com.example.heart_radio

import java.io.Closeable
import java.io.File
import java.util.ArrayDeque

/** Loss-tolerant diagnostics only. No sensor delivery or reminder journal writes belong here. */
class BatchedDiagnosticWriter(
    private val maxRecords:Int=256,
    private val maxCharacters:Int=262_144,
    private val maxRecordCharacters:Int=32_768,
    private val batchDelayMs:Long=500,
    private val append:(File,String)->Unit={file,text -> file.parentFile?.mkdirs();file.appendText(text,Charsets.UTF_8)}
):Closeable {
    private data class Entry(val file:File,val text:String,val important:Boolean)
    private val signal=Object()
    private val queue=ArrayDeque<Entry>()
    private var characters=0
    private var closing=false
    private var worker:Thread?=null
    @Volatile var droppedRecords=0L;private set
    @Volatile var failedRecords=0L;private set
    @Volatile var lastError="";private set
    init{require(maxRecords>0&&maxCharacters>0&&maxRecordCharacters>0&&batchDelayMs>=0)}

    /** Immutable text and destination are captured by the caller; never reads mutable sensor state. */
    fun offer(file:File,text:String,important:Boolean=false):Boolean = synchronized(signal){
        val line=if(text.endsWith('\n'))text else "$text\n"
        if(closing||line.length>maxRecordCharacters||line.length>maxCharacters){droppedRecords++;return@synchronized false}
        while(queue.size>=maxRecords||characters+line.length>maxCharacters){
            // Preserve error/lifecycle records in preference to routine per-event diagnostics.
            val victim=if(important)queue.firstOrNull{!it.important} else null
            if(victim==null){droppedRecords++;return@synchronized false}
            queue.remove(victim);characters-=victim.text.length;droppedRecords++
        }
        queue.addLast(Entry(file,line,important));characters+=line.length
        if(worker==null)worker=Thread({runWriter()},"watch-diagnostics").apply{isDaemon=true;start()}
        signal.notifyAll();true
    }

    private fun runWriter(){
        while(true){
            val batch=synchronized(signal){
                while(queue.isEmpty()&&!closing)signal.wait()
                if(queue.isEmpty()&&closing)return
                val deadline=System.nanoTime()+batchDelayMs*1_000_000
                while(!closing&&queue.size<64){
                    val left=deadline-System.nanoTime()
                    if(left<=0)break
                    signal.wait(left/1_000_000,(left%1_000_000).toInt())
                }
                val values=ArrayList<Entry>()
                repeat(minOf(64,queue.size)){values.add(queue.removeFirst())}
                characters-=values.sumOf{it.text.length}
                values
            }
            // Per-file order is retained even when records for different files interleave.
            batch.groupBy{it.file}.forEach{(file,rows)->
                try{append(file,rows.joinToString(""){it.text})}
                catch(error:Exception){failedRecords+=rows.size;lastError=error.javaClass.simpleName}
            }
        }
    }

    /** Controlled shutdown/tests only; live service callbacks never wait for diagnostic I/O. */
    override fun close(){
        val thread=synchronized(signal){closing=true;signal.notifyAll();worker}
        if(thread!==Thread.currentThread()){thread?.join(5000);check(thread?.isAlive!=true){"Diagnostic writer did not stop"}}
    }
}
