package com.example.heart_radio

import java.io.File

/** One process-wide writer; service stop does not discard another service's diagnostics. */
object WatchDiagnostics {
    private val stores=linkedMapOf<File,DiagnosticRetention>()
    private val writer=BatchedDiagnosticWriter(append={file,text->
        val root=requireNotNull(file.canonicalFile.parentFile?.parentFile)
        stores.getOrPut(root){DiagnosticRetention(root)}.append(file,text)
    })
    @Volatile private var submissionError=""
    fun append(file:File,text:String,important:Boolean=false):Boolean = try{writer.offer(file,text,important)}
        catch(error:Exception){submissionError=error.javaClass.simpleName;false}
    fun error():String = listOfNotNull(
        submissionError.takeIf{it.isNotEmpty()}?.let{"诊断提交失败：$it"},
        writer.lastError.takeIf{it.isNotEmpty()}?.let{"诊断写入失败：$it（${writer.failedRecords}条）"},
        writer.droppedRecords.takeIf{it>0}?.let{"诊断队列已丢弃${it}条；业务传输不受此队列控制"}
    ).joinToString("；")
}
