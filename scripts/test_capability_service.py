"""Ownership, bounded lifetime and preferences; no actual watch operations."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from capability_service import CapabilityService, ServiceError, atomic_json
from capability_duration import read_duration


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = CapabilityService(self.temp.name)
        self.service._windows = Mock()
        self.service._windows.mutex.side_effect = lambda *args: nullcontext()
        self.service._windows.process.side_effect = OSError('gone')
        override = patch.object(self.service, 'bridge_state', return_value=None)
        override.start(); self.addCleanup(override.stop)
        self.info = dict(pid=100, creationTicks='12345', executable=str(Path(self.temp.name) / 'python.exe'),
                         instanceId='a'*32, runId='20261006-120000-aaaaaaaa', seconds=1800,
                         expiresAtMs=time.time_ns() // 1000000 + 1800000)

    def register(self):
        atomic_json(self.service.metadata, self.info)
        self.service._windows.process.side_effect = None
        self.service._windows.process.return_value = dict(creationTicks=12345, executable=self.info['executable'])

    def test_default_and_saved_preference_do_not_launch_or_stop(self):
        with patch('capability_service.subprocess.Popen') as launch:
            self.assertFalse(self.service.preference()['enabledOnStart'])
            self.assertEqual(self.service.start(if_enabled=True)['action'], 'disabled')
            self.register()
            self.service.save(dict(enabledOnStart=True))
            self.service.save(dict(enabledOnStart=False))
            self.assertTrue(self.service.status()['running'])
            self.assertFalse((self.service.directory(self.info) / 'stop.request').exists())
            launch.assert_not_called()
        for invalid in ({}, dict(enabledOnStart=1), dict(enabledOnStart=True, seconds=99), None):
            with self.assertRaises(ValueError): self.service.save(invalid)

    def test_reuse_preserves_process_deadline_and_metadata(self):
        self.register()
        self.service.bridge_state.return_value = dict(instanceId='a'*32, connected=True)
        before = self.service.metadata.read_bytes()
        with patch('capability_service.subprocess.Popen') as launch:
            result = self.service.start(seconds=10)
            launch.assert_not_called()
        self.assertEqual(result['action'], 'reused')
        self.assertEqual(result['expiresAtMs'], self.info['expiresAtMs'])
        self.assertTrue(result['connected'])
        self.assertEqual(self.service.metadata.read_bytes(), before)

    def test_saved_duration_applies_to_owned_service_and_reuse_does_not_reset_it(self):
        self.register()
        saved=self.service.save_duration(dict(unlimited=False,amount=10,unit='minutes'))
        self.assertEqual(read_duration(self.service.root),saved)
        self.assertEqual(self.service.info()['seconds'],600)
        self.assertEqual(self.service.info()['expiresAtMs'],saved['savedMs']+600000)
        before=self.service.metadata.read_bytes()
        result=self.service.start()
        self.assertEqual(result['action'],'reused')
        self.assertEqual(result['configuredSeconds'],600)
        self.assertEqual(result['durationSeconds'],600)
        self.assertEqual(self.service.metadata.read_bytes(),before)

    def test_unlimited_removes_current_deadline_and_resaving_limit_restarts_from_save(self):
        self.register()
        first=self.service.save_duration(dict(unlimited=False,amount=1.5,unit='hours'))
        second=self.service.save_duration(dict(unlimited=False,amount=1.5,unit='hours'))
        self.assertNotEqual(first['revision'],second['revision'])
        self.assertEqual(self.service.info()['expiresAtMs'],second['savedMs']+5400000)
        self.service.save_duration(dict(unlimited=True))
        self.assertIsNone(self.service.info()['expiresAtMs'])
        self.assertEqual(self.service.info()['seconds'],0)
        self.assertTrue(self.service.status()['running'])

    def test_saving_duration_when_stopped_does_not_launch_and_status_requires_bridge_ack(self):
        with patch('capability_service.subprocess.Popen') as launch:
            saved=self.service.save_duration(dict(unlimited=False,amount=.5,unit='minutes'))
            launch.assert_not_called()
        self.assertFalse(self.service.status()['running'])
        self.register()
        self.service.bridge_state.return_value=dict(instanceId='a'*32,serverRemainingSeconds=20,
             serverLimit=dict(seconds=30,revision='previous',endsMs=5000))
        self.assertFalse(self.service.status()['durationApplied'])
        self.service.bridge_state.return_value['serverLimit']['revision']=saved['revision']
        self.assertTrue(self.service.status()['durationApplied'])

    def test_pid_reuse_cannot_write_stop_or_terminate_unrelated_process(self):
        self.register()
        self.service._windows.process.return_value['creationTicks'] = 54321
        with patch('capability_service.subprocess.Popen') as launch:
            self.assertEqual(self.service.stop()['action'], 'already_stopped')
            launch.assert_not_called()
        self.assertFalse((self.service.directory(self.info) / 'stop.request').exists())

    def test_instance_identity_mismatch_does_not_claim_watch_connected(self):
        self.register()
        self.service.bridge_state.return_value = dict(instanceId='b'*32, connected=True)
        result = self.service.status()
        self.assertEqual(result['state'], 'starting')
        self.assertFalse(result['connected'])

    def test_expired_unexpected_exit_and_foreign_bridge_are_distinct(self):
        atomic_json(self.service.metadata, self.info)
        self.assertEqual(self.service.status()['state'], 'exited')
        self.info['expiresAtMs'] = 1
        atomic_json(self.service.metadata, self.info)
        self.assertEqual(self.service.status()['state'], 'expired')
        self.service.bridge_state.return_value = dict(instanceId='b'*32, connected=False)
        self.assertEqual(self.service.status()['state'], 'port_conflict')

    def test_stop_waits_for_owned_exit_and_reports_offline_delivery(self):
        self.register()
        directory = self.service.directory(self.info); directory.mkdir(parents=True)
        (directory / 'pc-bridge.jsonl').write_text(json.dumps(dict(kind='watch_stop_result', status='watch_offline'))+'\n')
        self.service._windows.process.side_effect = [dict(creationTicks=12345, executable=self.info['executable']), OSError('gone'), OSError('gone'), OSError('gone')]
        result = self.service.stop()
        self.assertEqual(result['watchStopStatus'], 'watch_offline')
        self.assertFalse(result['running'])
        self.assertTrue((directory / 'stop.request').exists())

    def test_corrupt_run_id_cannot_address_an_outside_stop_file(self):
        self.info['runId'] = '../outside'
        atomic_json(self.service.metadata, self.info)
        with self.assertRaises(ServiceError): self.service.stop()
        self.assertFalse((Path(self.temp.name) / 'outside').exists())

    def test_stop_receipt_survives_rotation_and_compressed_final_segment(self):
        from diagnostic_log import DiagnosticLog,maintain
        import time
        self.register()
        directory=self.service.directory(self.info);directory.mkdir(parents=True)
        path=directory/'pc-bridge.jsonl';log=DiagnosticLog(path,max_bytes=10)
        log.write(json.dumps(dict(kind='watch_stop_result',status='watch_offline'))+'\n')
        log.write(json.dumps(dict(kind='bridge_stop'))+'\n');log.close()
        maintain(self.service.root,now=time.time()+3*86400)
        self.assertFalse(path.exists())
        self.service._windows.process.side_effect=[dict(creationTicks=12345,executable=self.info['executable']),OSError('gone'),OSError('gone'),OSError('gone')]
        self.assertEqual(self.service.stop()['watchStopStatus'],'watch_offline')

    def test_port_conflict_fails_before_launch_and_keeps_preference(self):
        self.service.save(dict(enabledOnStart=True))
        paired = self.service.root / '.local-wifi'; paired.mkdir()
        for name in ('config.json', 'cert.pem', 'key.pem'): (paired / name).touch()
        with patch.object(self.service, 'check_ports', side_effect=ServiceError('occupied')), patch('capability_service.subprocess.Popen') as launch:
            with self.assertRaises(ServiceError): self.service.start()
            launch.assert_not_called()
        self.assertTrue(self.service.preference()['enabledOnStart'])


if __name__ == '__main__':
    unittest.main()
