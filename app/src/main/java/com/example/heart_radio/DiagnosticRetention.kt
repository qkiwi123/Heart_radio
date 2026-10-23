package com.example.heart_radio

import java.io.File
import java.nio.file.Files

/** Invoked only by the serial diagnostic worker. Business data and preferences never enter this store. */
class DiagnosticRetention(
    root:File,
    private val now:()->Long=System::currentTimeMillis,
    private val maxFileBytes:Long=2L*1024*1024,
    private val maxTotalBytes:Long=32L*1024*1024,
    private val retentionMs:Long=7L*24*60*60*1000,
    private val scanIntervalMs:Long=60L*60*1000
) {
    private val root=root.canonicalFile
    private val active=linkedMapOf<String,File>()
    private var scanned=false
    private var lastScan=0L
    private var sequence=0L
    var deletedFiles=0L;private set
    var bytesAfterCleanup=0L;private set
    private val session=Regex("session-[0-9]+-[0-9]+\\.jsonl")
    private val part=Regex("(.+\\.jsonl)\\.part-[0-9]+-[0-9]+\\.jsonl")
    init{require(maxFileBytes>0&&maxTotalBytes>=maxFileBytes&&retentionMs>0&&scanIntervalMs>=0)}

    private fun category(file:File):String? {
        val relative=root.toPath().relativize(file.canonicalFile.toPath())
        if(relative.nameCount!=2||relative.startsWith(".."))return null
        val folder=relative.getName(0).toString();val name=relative.getName(1).toString()
        val base=part.matchEntire(name)?.groupValues?.get(1)?:name
        return when {
            folder=="logs"&&(session.matches(base)||base=="lifecycle.jsonl")->"heart"
            folder=="rfcomm-probe"&&base=="events.jsonl"->"rfcomm"
            folder=="capability-probe"&&base=="requests.jsonl"->"requests"
            folder=="capability-probe"&&base=="bridge-events.jsonl"->"bridge"
            else->null
        }
    }
    private fun managedFiles():List<File> = listOf("logs","rfcomm-probe","capability-probe").flatMap{folder->
        val dir=File(root,folder)
        // An alias directory could point at unrelated data. It is never a cleanup root.
        if(dir.exists()&&(Files.isSymbolicLink(dir.toPath())||dir.canonicalFile.parentFile!=root))emptyList()
        else dir.listFiles()?.filter{it.isFile&&!Files.isSymbolicLink(it.toPath())&&it.canonicalFile.parentFile==dir.canonicalFile&&category(it)!=null}?:emptyList()
    }
    private fun rotate(file:File) {
        val destination=File(file.parentFile,"${file.name}.part-${now().coerceAtLeast(0)}-${++sequence}.jsonl")
        check(!destination.exists()&&file.renameTo(destination)){"Diagnostic rotation failed"}
    }
    @Synchronized fun append(destination:File,text:String) {
        val file=destination.canonicalFile;val type=requireNotNull(category(file)){"Unmanaged diagnostic destination"}
        require(part.matchEntire(file.name)==null){"Cannot append to a closed diagnostic segment"}
        active[type]=file;file.parentFile?.mkdirs()
        val bytes=text.toByteArray(Charsets.UTF_8)
        val rotated=file.length()>0&&file.length()+bytes.size>maxFileBytes
        if(rotated)rotate(file)
        file.appendBytes(bytes)
        if(!scanned||now()-lastScan>=scanIntervalMs||rotated||file.length()>maxFileBytes)cleanup()
    }
    @Synchronized fun cleanup() {
        val stamp=now();lastScan=stamp;scanned=true
        if(File(root,".keep-logs").exists())return
        // Fixed active filenames can contain oversized logs from an older installed version.
        val fixed=listOf("logs/lifecycle.jsonl","rfcomm-probe/events.jsonl","capability-probe/requests.jsonl","capability-probe/bridge-events.jsonl")
        val latestSession=managedFiles().filter{session.matches(it.name)}.maxByOrNull{it.name.substringAfter("session-").substringBefore('-').toLongOrNull()?:0}
        val protected=(active.values+fixed.map{File(root,it).canonicalFile}+listOfNotNull(latestSession?.canonicalFile)).toSet()
        for(file in protected)if(file.isFile&&category(file)!=null&&file.length()>maxFileBytes)rotate(file)
        val files=managedFiles();var total=files.sumOf{it.length()}
        for(file in files.sortedWith(compareBy<File>{it.lastModified()}.thenBy{it.name})){
            if(file.canonicalFile in protected)continue
            if(stamp-file.lastModified()>=retentionMs||total>maxTotalBytes){
                val size=file.length()
                if(file.delete()){total-=size;deletedFiles++}
            }
        }
        bytesAfterCleanup=total
    }
}
