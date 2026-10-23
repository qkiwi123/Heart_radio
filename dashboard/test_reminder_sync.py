import tempfile
import unittest
from unittest.mock import Mock,patch
from reminders import Reminders
from reminder_sync import ReminderSync


class ReminderSyncTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.request=Mock(side_effect=lambda op,p:dict(status='issued',reminderId=p['reminderId']))
        self.reminders=Reminders(folder.name,self.request)
        for n in range(9):self.reminders.send(dict(reminderId='r'+str(n),title='保留标题',body='保留正文',ttlMs=60000))
        self.request.reset_mock();self.context=Mock(return_value=('rfcomm','connection1','gate1'));self.now=100
        self.sync=ReminderSync(self.reminders,self.context,clock=lambda:self.now)

    def test_reconnect_pulls_deletion_in_small_batches_without_any_effect(self):
        self.request.side_effect=lambda op,p:dict(status='deleted',reminderId=p['reminderId'],deletedAtMs=1)
        self.sync.step();self.assertEqual(self.request.call_count,4)
        self.sync.step();self.sync.step();self.assertEqual(self.request.call_count,9)
        self.assertEqual(self.sync.state()['status'],'complete')
        self.assertTrue(all(call.args[0]=='reminder_status' for call in self.request.call_args_list))
        self.assertTrue(all(row['params']['body']=='保留正文' for row in self.reminders.rows.values()))
        self.assertTrue(all(self.reminders.status(identity)['status']=='deleted' for identity in self.reminders.rows))
        self.sync.step();self.assertEqual(self.request.call_count,9)

    def test_closed_gate_waits_and_new_connection_queries_again(self):
        self.context.return_value=None;self.sync.step();self.request.assert_not_called()
        self.context.return_value=('wifi','connection1','gate1')
        for _ in range(3):self.sync.step()
        self.assertEqual(self.request.call_count,9)
        self.context.return_value=('wifi','connection2','gate1');self.sync.step()
        self.assertEqual(self.request.call_count,13)

    def test_old_route_response_is_discarded_and_new_route_can_retry(self):
        def changed(op,p):
            self.context.return_value=('wifi','connection2','gate1')
            return dict(status='deleted',reminderId=p['reminderId'],deletedAtMs=1)
        self.request.side_effect=changed;self.sync.step()
        self.assertTrue(all('deletedAtMs' not in row.get('receipt',{}) for row in self.reminders.rows.values()))
        self.assertEqual(self.sync.state()['checked'],0)
        self.request.side_effect=lambda op,p:dict(status='deleted',reminderId=p['reminderId'],deletedAtMs=2)
        self.sync.step();self.assertEqual(self.sync.state()['checked'],4)

    def test_not_found_is_checked_but_never_inferred_deleted(self):
        self.request.side_effect=lambda op,p:dict(status='not_found')
        self.sync.step();self.assertEqual(self.sync.state()['notFound'],4)
        rows=[row for row in self.reminders.rows.values() if row.get('watchRecordStatus')=='not_found']
        self.assertEqual(len(rows),4);self.assertTrue(all(row['params']['body']=='保留正文' for row in rows))
        self.assertTrue(all(row['receipt']['status']=='issued' for row in rows))

    def test_transient_failure_retries_at_most_three_times(self):
        self.request.side_effect=lambda op,p:dict(status='outcome_unknown_timeout')
        for _ in range(3):self.sync.step()
        self.assertEqual(self.request.call_count,9)
        self.sync.step();self.assertEqual(self.request.call_count,9)
        for _ in range(2):
            self.now+=16
            for _ in range(3):self.sync.step()
        self.assertEqual(self.request.call_count,27);self.assertEqual(self.sync.state()['failed'],9)
        self.assertEqual(self.sync.state()['status'],'partial')
        self.sync.step();self.assertEqual(self.request.call_count,27)

    def test_stop_prevents_requests(self):
        self.sync.stop();self.sync.step();self.request.assert_not_called()

    def test_completed_pass_does_not_spawn_workers_or_poll_watch_again(self):
        for _ in range(3):self.sync.step()
        before=self.request.call_count
        with patch('reminder_sync.threading.Thread') as worker:
            for _ in range(50):self.sync.tick()
            worker.assert_not_called()
        self.assertEqual(self.request.call_count,before)

    def test_unconfirmed_gate_after_one_batch_pauses_until_ready(self):
        self.sync.step();self.assertEqual(self.request.call_count,4)
        token=self.context.return_value;self.context.return_value=None;self.sync.tick()
        self.assertEqual(self.sync.state()['status'],'waiting');self.assertEqual(self.request.call_count,4)
        self.context.return_value=token;self.sync.step();self.assertEqual(self.request.call_count,8)

    def test_batch_uses_one_atomic_commit_and_storage_failure_stops_sync(self):
        self.reminders.save=Mock(side_effect=self.reminders.save)
        self.sync.step();self.reminders.save.assert_called_once()
        self.reminders.save=Mock(side_effect=OSError('disk full'))
        self.sync.step();self.assertEqual(self.sync.state()['status'],'storage_error');self.assertTrue(self.sync.stopped)


if __name__=='__main__':unittest.main()
