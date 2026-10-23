package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class ReminderJournalTest {
    private fun row(id:String="one",hash:String?="same",expiry:Long=1000)=ReminderJournal.Entry(id,hash,expiry,id)
    @Test fun duplicateAcrossAdaptersAndRestartNeverReservesAgain(){
        val original=ReminderJournal<String>();assertEquals("reserved",original.reserve(row(),1))
        assertEquals("duplicate",original.reserve(row(expiry=5000),2))
        val restored=ReminderJournal<String>();original.entries().forEach{restored.restore(it)}
        assertEquals("duplicate",restored.reserve(row(),2000));assertEquals(1000L,restored.get("one")!!.expiresAtMs)
    }
    @Test fun reusedIdentityWithDifferentEffectsOrContentIsAConflict(){
        val ledger=ReminderJournal<String>();ledger.reserve(row(),1)
        assertEquals("reminder_id_conflict",ledger.reserve(row(hash="other"),2))
        assertEquals("same",ledger.get("one")!!.fingerprint)
    }
    @Test fun legacyIdsRemainDuplicateWithoutRepeatingOldEffects(){
        val ledger=ReminderJournal<String>();ledger.restore(row(hash=null))
        assertEquals("duplicate",ledger.reserve(row(hash="new"),5))
    }
    @Test fun fullLedgerKeepsAllUnexpiredIdentities(){
        val ledger=ReminderJournal<String>(2);ledger.reserve(row("one"),1);ledger.reserve(row("two"),1)
        assertEquals("reminder_ledger_full",ledger.reserve(row("three"),2));assertNotNull(ledger.get("one"))
        assertEquals("reserved",ledger.reserve(row("three",expiry=3000),1001));assertNull(ledger.get("one"))
    }
    @Test fun deletionErasesOnlySelectedContentAndKeepsRetryProtectionAfterRestart(){
        val ledger=ReminderJournal<String>();ledger.reserve(row("one"),1);ledger.reserve(row("two"),1)
        var commits=0
        val deleted=ledger.redact(setOf("one"),{"deleted"},{commits++})
        assertEquals(listOf("one"),deleted.map{it.id});assertEquals(1,commits)
        assertEquals("deleted",ledger.get("one")!!.value);assertEquals("two",ledger.get("two")!!.value)
        val restored=ReminderJournal<String>();ledger.entries().forEach{restored.restore(it)}
        assertEquals("duplicate",restored.reserve(row("one",expiry=5000),2))
        assertEquals("reminder_id_conflict",restored.reserve(row("one",hash="other"),2))
        assertEquals(1000L,restored.get("one")!!.expiresAtMs)
    }
    @Test fun failedDeletionRestoresAllOriginalContentAndOrder(){
        val ledger=ReminderJournal<String>();ledger.reserve(row("one"),1);ledger.reserve(row("two"),1)
        val original=ledger.entries()
        try{ledger.redact(setOf("one","two"),{"deleted"},{throw java.io.IOException("disk full")});fail("expected storage failure")}
        catch(_:java.io.IOException){}
        assertEquals(original,ledger.entries())
    }
    @Test fun missingSelectionDoesNotWriteOrDeleteAnything(){
        val ledger=ReminderJournal<String>();ledger.reserve(row(),1)
        assertTrue(ledger.redact(setOf("missing"),{"deleted"},{fail("unexpected write")}).isEmpty())
        assertEquals("one",ledger.get("one")!!.value)
    }
    @Test fun expiredDeletedIdentityCanBeEvictedWhenLedgerFills(){
        val ledger=ReminderJournal<String>(1);ledger.reserve(row(),1)
        ledger.redact(setOf("one"),{"deleted"},{})
        assertEquals("reminder_ledger_full",ledger.reserve(row("two",expiry=5000),2))
        assertEquals("reserved",ledger.reserve(row("two",expiry=5000),1001))
        assertNull(ledger.get("one"))
    }
}
