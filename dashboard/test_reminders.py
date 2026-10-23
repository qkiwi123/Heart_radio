import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
from reminders import Reminders


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.request=Mock(side_effect=lambda op,p:dict(status='issued',reminderId=p['reminderId'],title=p.get('title'),vibrationIssued=True))
        self.service=Reminders(self.folder.name,self.request)
        self.value=dict(reminderId='same',title='测试',body='这是提醒',ttlMs=60000)
    def test_duplicate_after_reconnect_and_process_restore_never_resends(self):
        first=self.service.send(self.value);expiry=first['pcExpiresAtMs']
        second=self.service.send(dict(self.value,ttlMs=300000));self.assertTrue(second['duplicate'])
        restored=Reminders(self.folder.name,self.request);third=restored.send(self.value)
        self.assertTrue(third['duplicate']);self.assertEqual(third['pcExpiresAtMs'],expiry);self.request.assert_called_once()
    def test_same_id_different_content_or_effect_is_rejected(self):
        self.service.send(self.value)
        for changed in [dict(self.value,body='别的内容'),dict(self.value,vibrate=False)]:
            with self.assertRaises(ValueError):self.service.send(changed)
        self.request.assert_called_once()
    def test_uncertain_result_is_preserved_without_replay(self):
        self.request.return_value=dict(status='outcome_unknown_timeout');self.request.side_effect=None
        self.assertEqual(self.service.send(self.value)['status'],'outcome_unknown_timeout')
        self.assertTrue(Reminders(self.folder.name,self.request).send(self.value)['duplicate']);self.request.assert_called_once()
    def test_interrupted_local_attempt_is_unknown_on_restore(self):
        self.service.send(self.value);p=self.service.path;rows=json.loads(p.read_text(encoding='utf-8'));rows['same']['attemptStatus']='sending';rows['same'].pop('receipt',None);p.write_text(json.dumps(rows),encoding='utf-8')
        self.assertEqual(Reminders(self.folder.name,self.request).status('same')['status'],'outcome_unknown_after_restart')
        self.request.assert_called_once()
    def test_ack_arriving_before_request_reply_is_not_erased(self):
        def race(op,p):
            self.service.observe(dict(reminderId=p['reminderId'],acknowledgedAtMs=123,status='issued'))
            return dict(reminderId=p['reminderId'],status='issued')
        self.request.side_effect=race
        self.assertEqual(self.service.send(self.value)['status'],'acknowledged')

    def test_sound_completion_before_reply_or_late_event_does_not_regress(self):
        def race(op,p):
            self.service.observe(dict(reminderId=p['reminderId'],soundIssued=True,soundEventSeq=2,soundStatus='completed',soundCompletedAtMs=123))
            return dict(reminderId=p['reminderId'],status='issued',soundIssued=False,soundEventSeq=0,soundStatus='preparing')
        self.request.side_effect=race
        result=self.service.send(self.value)
        self.assertTrue(result['receipt']['soundIssued']);self.assertEqual(result['receipt']['soundStatus'],'completed')
        self.service.observe(dict(reminderId='same',soundIssued=True,soundEventSeq=1,soundStatus='started'))
        self.assertEqual(self.service.rows['same']['receipt']['soundStatus'],'completed')
    def test_repeated_cached_events_do_not_claim_new_receipt_age(self):
        self.service.send(self.value);event=dict(reminderId='same',acknowledgedAtMs=123)
        self.service.observe(event);before=self.service.rows['same']['receiptReceivedAtMs']
        self.service.rows['same']['receiptReceivedAtMs']=before-5000
        self.service.observe(event);self.assertEqual(self.service.rows['same']['receiptReceivedAtMs'],before-5000)
    def test_cached_receipt_and_templates_work_while_gate_closed(self):
        self.service.send(self.value);self.request.reset_mock()
        self.assertEqual(self.service.status('same')['status'],'issued');self.assertEqual(self.service.state()['defaultTemplate'],'strong')
        self.request.assert_not_called()
        self.request.return_value=dict(status='interaction_disabled');self.request.side_effect=None
        self.assertEqual(self.service.status('same',fresh=True)['status'],'interaction_disabled')
    def test_save_failure_prevents_wire_effect_and_preserves_file(self):
        def failed(*args):raise OSError('test disk failure')
        service=Reminders(self.folder.name,self.request,save=failed)
        with self.assertRaises(OSError):service.send(self.value)
        self.request.assert_not_called();self.assertTrue(service.error)
    def test_concurrent_duplicate_reserves_only_once(self):
        entered=threading.Event();release=threading.Event()
        def paused(op,p):entered.set();release.wait(2);return dict(status='issued',reminderId=p['reminderId'])
        self.request.side_effect=paused
        thread=threading.Thread(target=self.service.send,args=(self.value,));thread.start();self.addCleanup(thread.join)
        self.assertTrue(entered.wait(1))
        try:self.assertTrue(self.service.send(self.value)['duplicate']);self.request.assert_called_once()
        finally:release.set();thread.join(2)
    def test_oversized_or_corrupt_journal_is_preserved_and_fails_closed(self):
        p=self.service.path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{bad')
        service=Reminders(self.folder.name,self.request)
        with self.assertRaises(OSError):service.send(self.value)
        self.assertEqual(p.read_text(),'{bad');self.request.assert_not_called()
    def test_explicit_refresh_can_read_a_legacy_watch_id_without_local_send_record(self):
        self.request.side_effect=None;self.request.return_value=dict(status='issued',reminderId='legacy',acknowledgedAtMs=123)
        value=self.service.status('legacy',fresh=True)
        self.assertEqual(value['status'],'acknowledged');self.assertFalse(value['cached']);self.assertEqual(self.service.rows,{})

    def test_deletion_is_terminal_and_does_not_remove_pc_content(self):
        self.service.send(self.value)
        self.service.observe(dict(reminderId='same',status='issued',acknowledgedAtMs=5))
        self.service.observe(dict(reminderId='same',status='deleted',deletedAtMs=10))
        self.service.observe(dict(reminderId='same',status='issued',displayedAtMs=4))
        row=self.service.status('same')
        self.assertEqual(row['status'],'deleted');self.assertEqual(row['receipt']['status'],'deleted')
        self.assertEqual(row['params']['body'],self.value['body'])
        self.assertEqual(Reminders(self.folder.name,self.request).status('same')['status'],'deleted')

    def test_not_found_check_is_distinct_from_deletion(self):
        self.service.send(self.value);self.request.side_effect=None;self.request.return_value=dict(status='not_found')
        self.assertEqual(self.service.status('same',True)['status'],'not_found')
        row=self.service.status('same')
        self.assertEqual(row['status'],'issued');self.assertEqual(row['watchRecordStatus'],'not_found')
        self.assertTrue(row['watchCheckedAtMs']);self.assertEqual(row['params']['body'],self.value['body'])

    def test_history_pages_all_retained_rows_and_filters_unicode_content(self):
        for n in range(45):self.service.send(dict(self.value,reminderId='r'+str(n),body='目标内容' if n==0 else '其他内容'))
        self.service.observe(dict(reminderId='r0',status='deleted',deletedAtMs=10))
        pages=[self.service.history(dict(page=str(page))) for page in (1,2,3)]
        ids=[row['reminderId'] for page in pages for row in page['reminders']]
        self.assertEqual(len(set(ids)),45);self.assertEqual(pages[0]['total'],45);self.assertEqual(pages[0]['capacity'],128)
        result=self.service.history(dict(q='目标',status='deleted',page='8'))
        self.assertEqual(result['matched'],1);self.assertEqual(result['page'],1)
        self.assertEqual(result['reminders'][0]['reminderId'],'r0')
        self.request.reset_mock();self.service.history({});self.request.assert_not_called()
        for value in ({'page':'0'},{'page':'no'},{'pageSize':'51'},{'unknown':'x'},{'q':'a'*201}):
            with self.assertRaises(ValueError):self.service.history(value)

    def test_deleted_records_still_count_toward_capacity_until_expired(self):
        for n in range(128):self.service.send(dict(self.value,reminderId='r'+str(n)))
        self.service.observe(dict(reminderId='r0',status='deleted',deletedAtMs=10))
        with self.assertRaises(ValueError):self.service.send(dict(self.value,reminderId='full'))
        self.service.rows['r0']['pcExpiresAtMs']=0
        self.service.send(dict(self.value,reminderId='replacement'))
        self.assertEqual(len(self.service.rows),128);self.assertNotIn('r0',self.service.rows)


if __name__=='__main__':unittest.main()
