package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.nio.file.Files

class DiagnosticRetentionTest {
    private fun fixture(block:(File)->Unit){
        val root=Files.createTempDirectory("watch-retention-").toFile()
        try{block(root)}finally{root.deleteRecursively()}
    }
    private fun file(root:File,name:String,text:String,time:Long):File = File(root,name).apply{
        parentFile!!.mkdirs();writeText(text,Charsets.UTF_8);setLastModified(time)
    }
    @Test fun rotationKeepsUnicodeRecordsAndDoesNotAppendToClosedSegments()=fixture{root->
        var clock=100_000L
        val store=DiagnosticRetention(root,now={clock},maxFileBytes=40,maxTotalBytes=10_000,retentionMs=1_000_000)
        val log=File(root,"logs/session-100-200.jsonl")
        val expected=(1..10).map{"中文记录$it\n"}
        expected.forEach{store.append(log,it);clock++}
        val parts=log.parentFile!!.listFiles()!!.filter{it.name.contains(".part-")}.sortedBy{it.name.substringAfter(".part-").substringBefore('-').toLong()}
        assertTrue(parts.isNotEmpty());assertEquals(expected.joinToString(""),parts.joinToString(""){it.readText()}+log.readText())
        try{store.append(parts.first(),"must reject");fail("closed segment accepted")}catch(_:IllegalArgumentException){}
    }
    @Test fun expiredDiagnosticIsRemovedButBusinessAndConfigurationRemain()=fixture{root->
        val old=file(root,"logs/session-1-1.jsonl","old\n",1)
        val business=listOf("agent-reminders/ledger.json","rfcomm-probe.json","capability-bridge.json","shared_prefs/state.xml","logs/unknown.txt")
            .map{file(root,it,"preserved\n",1)}
        val store=DiagnosticRetention(root,now={1_000_000L},retentionMs=1000)
        store.append(File(root,"logs/session-2-2.jsonl"),"current\n")
        assertFalse(old.exists());assertTrue(business.all{it.readText()=="preserved\n"})
    }
    @Test fun totalBudgetDeletesOldestClosedFilesAndProtectsActiveBase()=fixture{root->
        val first=file(root,"logs/session-1-1.jsonl","a".repeat(60),10)
        val second=file(root,"logs/session-2-2.jsonl","b".repeat(60),20)
        val store=DiagnosticRetention(root,now={100L},maxFileBytes=80,maxTotalBytes=100,retentionMs=10_000)
        val current=File(root,"logs/session-3-3.jsonl");store.append(current,"c".repeat(20))
        assertFalse(first.exists());assertTrue(second.exists());assertEquals(20L,current.length())
        assertTrue(store.bytesAfterCleanup<=100)
    }
    @Test fun liveFixedLogsRotateBeforeCapacityCleanup()=fixture{root->
        val old=file(root,"rfcomm-probe/events.jsonl","old".repeat(100),1)
        val store=DiagnosticRetention(root,now={1000L},maxFileBytes=40,maxTotalBytes=100,retentionMs=100_000)
        store.append(old,"fresh\n")
        assertEquals("fresh\n",old.readText());assertTrue(store.bytesAfterCleanup<=100)
    }
    @Test fun pinKeepsClosedFilesWhileAllowingFutureRotation()=fixture{root->
        file(root,".keep-logs","",1)
        val old=file(root,"logs/session-1-1.jsonl","old\n",1)
        val store=DiagnosticRetention(root,now={1000L},maxFileBytes=10,maxTotalBytes=20,retentionMs=10)
        val current=File(root,"logs/session-2-2.jsonl")
        store.append(current,"first\n");store.append(current,"second\n")
        assertTrue(old.exists());assertEquals(3,File(root,"logs").listFiles()!!.size)
    }
    @Test fun unmanagedAndEscapingDestinationsAreRejected()=fixture{root->
        val store=DiagnosticRetention(root)
        for(name in listOf("agent-reminders/ledger.json","rfcomm-probe.json","../outside.jsonl")){
            try{store.append(File(root,name),"never");fail(name)}catch(_:IllegalArgumentException){}
        }
        assertFalse(File(root,"agent-reminders/ledger.json").exists())
    }
    @Test fun scanRunsOnRollOrIntervalRatherThanEveryAppend()=fixture{root->
        var clock=100L
        val store=DiagnosticRetention(root,now={clock},retentionMs=1000,scanIntervalMs=10_000)
        val current=File(root,"logs/session-2-2.jsonl");store.append(current,"first\n")
        val old=file(root,"logs/session-1-1.jsonl","old\n",1)
        clock=2000;store.append(current,"second\n");assertTrue(old.exists())
        clock=20_000;store.append(current,"third\n");assertFalse(old.exists());assertTrue(current.exists())
    }
}
