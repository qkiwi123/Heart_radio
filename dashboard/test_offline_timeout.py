import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from offline_timeout import OfflineTimeout
from transport_access import TransportAccess


class OfflineShutdownTests(unittest.TestCase):
    def test_stopped_connection_manager_does_not_exempt_the_remaining_dashboard(self):
        with tempfile.TemporaryDirectory() as root:
            access=TransportAccess(root);access.stop();access.offline.start(0)
            with patch('transport_access.time.monotonic',return_value=600):access._check_offline_timeout()
            self.assertTrue(access.session['suspended'])
    def test_exact_boundary_then_recovery_and_new_loss(self):
        timer=OfflineTimeout();timer.start(10)
        self.assertFalse(timer.expired(609.99,False))
        self.assertTrue(timer.expired(610,False));self.assertFalse(timer.expired(999,False))
        timer.start(1000);self.assertFalse(timer.expired(1601,True))
        self.assertFalse(timer.expired(2000,False));self.assertTrue(timer.expired(2600,False))

    def test_shutdown_preserves_business_registers_and_cancels_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            access=TransportAccess(root);access.managed=True
            access.control(dict(action='heart_broadcast_on'))
            access.control(dict(action='agent_on'))
            files=[Path(root)/'runs'/name for name in ('heart-broadcast.json','agent-interaction.json')]
            # The interaction store uses its own established file name.
            files=list((Path(root)/'runs').glob('*.json'))
            before={p:p.read_bytes() for p in files}
            access.on_gate_closed=Mock();access.on_offline_timeout=Mock()
            access.offline.start(0)
            with patch('transport_access.time.monotonic',return_value=600):access._check_offline_timeout()
            self.assertTrue(access.session['suspended']);self.assertFalse(access.agent_enabled)
            self.assertTrue(access.manager_stop.is_set());access.on_gate_closed.assert_called_once()
            self.assertEqual(before,{p:p.read_bytes() for p in files})
            restored=TransportAccess(root)
            self.assertTrue(restored.session['suspended'])
            self.assertTrue(restored.cache.receiving)
            with patch.object(restored,'wifi_state',return_value=dict(connected=False)):
                self.assertTrue(restored.state()['projectSession']['suspended'])

    def test_selected_wifi_or_bluetooth_prevents_idle_exit(self):
        for route in ('rfcomm','wifi'):
            with self.subTest(route=route),tempfile.TemporaryDirectory() as root:
                access=TransportAccess(root);access.offline.start(0)
                access.selected=route;access.candidates={route:dict(connected=True)}
                with patch('transport_access.time.monotonic',return_value=900):access._check_offline_timeout()
                self.assertFalse(access.session['suspended']);self.assertFalse(access.manager_stop.is_set())

    def test_old_finite_interaction_deadline_is_not_renewed_by_reload(self):
        with tempfile.TemporaryDirectory() as root:
            access=TransportAccess(root);access.managed=True
            access.control(dict(action='agent_on'))
            fingerprint=access.interaction.register.fingerprint()
            deadlines=dict(access.interaction.register.deadlines)
            access.offline.start(0)
            with patch('transport_access.time.monotonic',return_value=600):access._check_offline_timeout()
            other=TransportAccess(root);other.managed=True;other._ensure_interaction()
            self.assertEqual(fingerprint,other.interaction.register.fingerprint())
            self.assertEqual(deadlines,other.interaction.register.deadlines)

    def test_manual_resume_keeps_local_receive_pause_and_repeated_start_does_not_extend_timer(self):
        with tempfile.TemporaryDirectory() as root:
            access=TransportAccess(root);access.managed=True;access.cache.receiving=False
            access.session=dict(suspended=True,reason='offline_timeout',timeoutSeconds=600,heartReceiveEnabled=False)
            with patch('transport_access.threading.Thread') as thread,patch('transport_access.time.monotonic',return_value=100):
                access.start()
                self.assertFalse(access.session['suspended']);self.assertFalse(access.cache.receiving)
                first=access.offline.waiting_since
                access.start();self.assertEqual(first,access.offline.waiting_since)
                thread.return_value.start.assert_called_once()

if __name__=='__main__':unittest.main()
