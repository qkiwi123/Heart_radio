package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.io.IOException
import java.nio.file.Files
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class BatchedDiagnosticWriterTest {
    private val a=File("session-a.jsonl")
    private val b=File("session-b.jsonl")
    private fun await(latch:CountDownLatch){assertTrue("worker did not reach expected state",latch.await(3,TimeUnit.SECONDS))}

    @Test fun batchesWithoutChangingPerFileOrderOrSessionDestination(){
        val writes=mutableListOf<Pair<File,String>>()
        val writer=BatchedDiagnosticWriter(batchDelayMs=5000,append={file,text->writes.add(file to text)})
        writer.offer(a,"a1");writer.offer(b,"b1");writer.offer(a,"a2\n");writer.offer(b,"b2")
        writer.close()
        assertEquals(listOf(a to "a1\na2\n",b to "b1\nb2\n"),writes)
    }

    @Test fun slowDiskDoesNotBlockTheProducerAndWritesRunOffItsThread(){
        val entered=CountDownLatch(1);val release=CountDownLatch(1);val offered=CountDownLatch(1)
        val caller=Thread.currentThread();var writingThread:Thread?=null
        val writer=BatchedDiagnosticWriter(batchDelayMs=0,append={_,_->writingThread=Thread.currentThread();entered.countDown();await(release)})
        try{
            assertTrue(writer.offer(a,"first"));await(entered)
            Thread{writer.offer(a,"second");offered.countDown()}.start()
            await(offered);assertNotSame(caller,writingThread)
        }finally{release.countDown();writer.close()}
    }

    @Test fun fullQueueDropsOnlyTheNewRoutineDiagnostic(){
        val entered=CountDownLatch(1);val release=CountDownLatch(1);val saved=mutableListOf<String>()
        val writer=BatchedDiagnosticWriter(maxRecords=2,batchDelayMs=0,append={_,text->
            if(text=="in-flight\n"){entered.countDown();await(release)}
            saved.add(text)
        })
        try{
            writer.offer(a,"in-flight");await(entered)
            assertTrue(writer.offer(a,"one"));assertTrue(writer.offer(a,"two"))
            assertFalse(writer.offer(a,"three"));assertEquals(1L,writer.droppedRecords)
        }finally{release.countDown();writer.close()}
        assertEquals("in-flight\none\ntwo\n",saved.joinToString(""))
    }

    @Test fun importantDiagnosticReplacesRoutineEntryButCannotGrowQueue(){
        val entered=CountDownLatch(1);val release=CountDownLatch(1);val saved=mutableListOf<String>()
        val writer=BatchedDiagnosticWriter(maxRecords=2,batchDelayMs=0,append={_,text->
            if(text=="in-flight\n"){entered.countDown();await(release)}
            saved.add(text)
        })
        try{
            writer.offer(a,"in-flight");await(entered)
            writer.offer(a,"routine");writer.offer(a,"error-one",true)
            assertTrue(writer.offer(a,"error-two",true));assertFalse(writer.offer(a,"error-three",true))
            assertEquals(2L,writer.droppedRecords)
        }finally{release.countDown();writer.close()}
        assertEquals("in-flight\nerror-one\nerror-two\n",saved.joinToString(""))
    }

    @Test fun totalCharacterBudgetAndPerRecordLimitAreEnforced(){
        val entered=CountDownLatch(1);val release=CountDownLatch(1)
        val writer=BatchedDiagnosticWriter(maxRecords=100,maxCharacters=8,maxRecordCharacters=6,batchDelayMs=0,append={_,text->
            if(text=="start\n"){entered.countDown();await(release)}
        })
        try{
            writer.offer(a,"start");await(entered)
            assertTrue(writer.offer(a,"12345"));assertFalse(writer.offer(a,"abc"))
            assertFalse(writer.offer(a,"123456"));assertEquals(2L,writer.droppedRecords)
        }finally{release.countDown();writer.close()}
    }

    @Test fun oneFileFailureIsCountedWithoutDiscardingTheOtherFile(){
        val saved=mutableListOf<String>()
        val writer=BatchedDiagnosticWriter(batchDelayMs=5000,append={file,text->
            if(file==a)throw IOException("full") else saved.add(text)
        })
        writer.offer(a,"a1");writer.offer(a,"a2");writer.offer(b,"b1");writer.close()
        assertEquals(2L,writer.failedRecords);assertEquals("IOException",writer.lastError)
        assertEquals(listOf("b1\n"),saved)
    }

    @Test fun rejectedOversizeRecordNeverStartsAnIoOperation(){
        val writer=BatchedDiagnosticWriter(maxRecordCharacters=4,append={_,_->fail("unexpected I/O")})
        assertFalse(writer.offer(a,"1234"));writer.close();assertEquals(1L,writer.droppedRecords)
    }

    @Test fun controlledShutdownDrainsAcceptedRecordsAndRejectsFurtherOffers(){
        val saved=mutableListOf<String>()
        val writer=BatchedDiagnosticWriter(batchDelayMs=5000,append={_,text->saved.add(text)})
        writer.offer(a,"final",true);writer.close()
        assertEquals("final\n",saved.joinToString(""));assertFalse(writer.offer(a,"after"))
    }

    @Test fun realUtf8FileKeepsEveryAcceptedRecordAcrossSeveralBatches(){
        val directory=Files.createTempDirectory("heartradio-diagnostics-")
        val file=directory.resolve("events.jsonl").toFile()
        val rows=(1..150).map{"{\"sequence\":$it,\"note\":\"心率诊断\"}"}
        val writer=BatchedDiagnosticWriter()
        try{
            rows.forEach{assertTrue(writer.offer(file,it))};writer.close()
            assertEquals(rows.joinToString("\n",postfix="\n"),file.readText(Charsets.UTF_8))
            assertEquals(0L,writer.droppedRecords);assertEquals(0L,writer.failedRecords)
        }finally{writer.close();Files.deleteIfExists(file.toPath());Files.deleteIfExists(directory)}
    }
}
