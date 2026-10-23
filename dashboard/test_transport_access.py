import tempfile
import unittest
from unittest.mock import Mock, patch
from transport_access import HeartCache, TransportAccess


class HeartCacheTests(unittest.TestCase):
    def setUp(self):
        self.cache=HeartCache()
        self.cache.observe(dict(event='hello',heartRunning=False,heartState='idle'))

    def event(self, **changes):
        return dict(event='heart',sequence=1,heartAttemptId='attempt',valid=True,bpm=75,accuracy=3,watchReceiptMs=100,**changes)

    def test_connection_and_heartbeats_never_create_measurements(self):
        self.assertFalse(self.cache.snapshot()['valid'])
        self.cache.observe(dict(event='heartbeat',heartRunning=True,heartState='waiting_first_data'))
        self.assertFalse(self.cache.snapshot()['valid'])
        self.assertEqual(self.cache.snapshot()['sensorEventCount'],0)

    def test_disconnection_and_reconnection_never_reuse_a_previous_value(self):
        self.cache.observe(self.event());self.assertEqual(self.cache.snapshot()['bpm'],75)
        self.cache.observe(dict(event='disconnected'))
        self.assertIsNone(self.cache.snapshot()['bpm'])
        self.cache.observe(dict(event='hello',heartRunning=True,heartState='ready'))
        self.assertIsNone(self.cache.snapshot()['bpm'])

    def test_stop_or_no_contact_invalidates_instead_of_showing_previous_value(self):
        self.cache.observe(self.event())
        self.cache.observe(dict(event='heart_state',heartRunning=False,heartState='stopped',heartStateMessage='已停止'))
        self.assertIsNone(self.cache.snapshot()['bpm'])
        self.assertEqual(self.cache.snapshot()['sensorEventCount'],1)

    def test_receiving_switch_does_not_request_or_replay_measurements(self):
        self.cache.observe(self.event());self.cache.set_receiving(False)
        self.cache.observe(self.event());self.cache.set_receiving(True)
        self.assertIsNone(self.cache.snapshot()['bpm'])
        self.cache.observe(self.event());self.assertTrue(self.cache.snapshot()['valid'])

    def test_old_sample_expires_even_when_link_is_connected(self):
        self.cache.observe(self.event())
        self.cache.state.last_event['pcMonotonicNs']-=31_000_000_000
        self.assertTrue(self.cache.snapshot()['connected'])
        self.assertIsNone(self.cache.snapshot()['bpm'])


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.access=TransportAccess(self.tmp.name)
        self.access.mode='rfcomm';self.access.probe=Mock();self.access.probe.state.return_value={}

    def test_local_gate_prevents_new_watch_queries_but_allows_cleanup(self):
        self.assertEqual(self.access.request('status')['status'],'interaction_disabled')
        self.assertEqual(self.access.request('sample',dict(sensorId=1,seconds=4))['status'],'interaction_disabled')
        self.access.probe.request.assert_not_called()
        self.access.request('cancel',dict(targetId='old'))
        self.assertEqual(self.access.probe.request.call_count,1)

    def test_open_gate_uses_same_request_identity_and_no_remote_hr_start(self):
        self.access.control(dict(action='agent_on'))
        self.access.request('sample',dict(sensorId=1,seconds=4),'identity')
        self.access.probe.request.assert_called_once_with(dict(op='sample',params=dict(sensorId=1,seconds=4),requestId='identity'))

    def test_connection_actions_validate_before_changing_state(self):
        for value in [dict(action='start_heart'),dict(action='agent_on',force=True),{}]:
            with self.assertRaises(ValueError):self.access.control(value)
        self.assertFalse(self.access.agent_enabled)

    def test_reminder_sync_context_requires_existing_route_and_confirmed_gate(self):
        self.assertIsNone(self.access.reminder_sync_context());self.access.probe.request.assert_not_called()
        self.access.managed=True;self.access.agent_enabled=True;self.access.selected='rfcomm'
        self.access.selected_key=1;self.access.selection_id='first'
        self.access.interaction=Mock();self.access.interaction.register.fingerprint.return_value='gate1'
        self.access.candidates={'rfcomm':dict(connected=True)}
        with patch.object(self.access,'_sync_confirmed',return_value=False):self.assertIsNone(self.access.reminder_sync_context())
        with patch.object(self.access,'_sync_confirmed',return_value=True):
            first=self.access.reminder_sync_context();self.access.selection_id='second'
            self.assertNotEqual(first,self.access.reminder_sync_context())
            self.access.agent_enabled=False;self.assertIsNone(self.access.reminder_sync_context())
        self.access.probe.request.assert_not_called()


if __name__=='__main__':unittest.main()
