import ctypes
import io
import json
import struct
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile
from rfcomm_probe import frame, read_frame, MAX_FRAME, Probe
from rfcomm_win import make_address, SockaddrBth


class Fragmented:
    def __init__(self, data): self.data = io.BytesIO(data)
    def recv(self, size): return self.data.read(min(size, 2))


class ProtocolTests(unittest.TestCase):
    def test_fragmented_chinese_roundtrip(self):
        value = dict(op='remind', params=dict(title='提醒', body='请起身活动。\n知道了'))
        self.assertEqual(read_frame(Fragmented(frame(value))), value)

    def test_reject_oversize_empty_and_nonobject(self):
        for raw in [struct.pack('!I', 0), struct.pack('!I', MAX_FRAME+1), struct.pack('!I', 2)+b'[]']:
            with self.assertRaises(ValueError): read_frame(Fragmented(raw))

    def test_truncated_frame_is_not_a_reply(self):
        with self.assertRaises(ConnectionError): read_frame(Fragmented(struct.pack('!I', 10)+b'{}'))

    def test_idle_and_partial_read_timeouts_preserve_frame(self):
        encoded=frame(dict(op='status',label='中文'))
        class Sleeping:
            def __init__(self):self.data=io.BytesIO(encoded);self.calls=0
            def recv(self,size):
                self.calls+=1
                if self.calls in (1,3,6):raise TimeoutError('sleeping watch')
                return self.data.read(min(size,2))
        self.assertEqual(read_frame(Sleeping(),lambda:True),dict(op='status',label='中文'))

    def test_idle_read_stops_at_experiment_deadline(self):
        class Idle:
            def recv(self,size):raise TimeoutError('idle')
        with self.assertRaises(TimeoutError):read_frame(Idle(),lambda:False)

    def test_socket_layout_and_guid_endianness(self):
        target = make_address('02:00:00:00:00:01', '749b3000-6f67-4d32-96dd-4d778c27a101')
        self.assertEqual(ctypes.sizeof(SockaddrBth), 30)
        self.assertEqual(SockaddrBth.address.offset, 2)
        self.assertEqual(target.address, 0x020000000001)
        self.assertEqual(bytes(target.service)[:4], bytes.fromhex('00309b74'))
        with self.assertRaises(ValueError): make_address('not-an-address', '749b3000-6f67-4d32-96dd-4d778c27a101')


class RequestTests(unittest.TestCase):
    def test_heart_sample_has_a_fifteen_second_bound_before_bluetooth(self):
        value=dict(op='heart_sample',params=dict(sensorId=0,seconds=15))
        self.assertEqual(self.probe.request(value)['status'],'watch_offline')
        for params in [dict(sensorId=0,seconds=16),dict(sensorId=0,seconds=True),dict(sensorId=0,seconds=15,continuous=True)]:
            with self.assertRaises(ValueError):self.probe.request(dict(op='heart_sample',params=params))
        self.assertEqual(self.probe.pending,{})
    def test_sync_snapshot_validation_precedes_any_bluetooth_send(self):
        from interaction_sync import InteractionRegister
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        probe=Probe({},Path(folder.name)/'log.jsonl');self.addCleanup(probe.stream.close)
        register=InteractionRegister('pc');register.edit(True,30000)
        params=dict(selectionId='a'*32,syncId='b'*32,state=register.wire())
        self.assertEqual(probe.request(dict(op='sync_interaction',params=params))['status'],'watch_offline')
        for bad in [dict(params,enabled=True),dict(params,syncId='bad'),dict(params,state={'watch':None})]:
            with self.assertRaises(ValueError):probe.request(dict(op='sync_interaction',params=bad))
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.probe=Probe({},Path(self.temp.name)/'pc.jsonl')

    def tearDown(self):
        self.probe.stream.close()
        self.temp.cleanup()

    def test_offline_request_is_never_queued(self):
        self.assertEqual(self.probe.request(dict(op='status')),dict(status='watch_offline'))
        self.assertEqual(self.probe.pending,{})

    def test_disconnect_does_not_replay_an_uncertain_reminder(self):
        probe=self.probe
        class Client:
            sends=0
            def sendall(self,payload):
                self.sends+=1
                probe.disconnected()
        client=Client()
        probe.client=client
        probe.connected=True
        value=probe.request(dict(op='remind',params=dict(reminderId='once',title='测试',body='一次发送',ttlMs=60000),requestId='one_time'))
        self.assertEqual(value['status'],'outcome_unknown_disconnected')
        self.assertEqual(client.sends,1)
        self.assertEqual(probe.pending,{})
        self.assertEqual(probe.request(dict(op='status'))['status'],'watch_offline')

    def test_arbitrary_commands_and_parameters_rejected(self):
        for value in [dict(op='shell'),dict(op='status',params=dict(token='unexpected')),
                      dict(op='status',requestId='../bad')]:
            with self.assertRaises(ValueError):self.probe.request(value)

    def test_duplicate_active_identity_rejected(self):
        self.probe.pending['active']={}
        with self.assertRaises(ValueError):self.probe.request(dict(op='status',requestId='active'))

    def test_failed_old_send_cannot_close_a_replacement_socket(self):
        probe=self.probe
        class New:
            closed=False
            def close(self):self.closed=True
        replacement=New()
        class Old(New):
            def sendall(self,data):probe.client=replacement;raise OSError('old socket closed')
        old=Old();probe.client=old;probe.connected=True
        self.assertEqual(probe.request(dict(op='status'))['status'],'outcome_unknown_disconnected')
        self.assertTrue(old.closed);self.assertFalse(replacement.closed)

    def test_heart_observation_is_explicitly_cached_and_cleared_on_disconnect(self):
        probe=self.probe;probe.connected=True;probe.connection_serial=1
        probe.remember_heart_observation(dict(heartDiagnostic=dict(state='ready'),bpm=75))
        probe.remember_heart_observation(dict(event='heart',bpm=76))
        observed=probe.state()['heartObservation']
        self.assertEqual(observed['diagnostic']['state'],'ready')
        self.assertEqual(observed['bpmAtObservation'],75)
        self.assertTrue(observed['cached']);self.assertFalse(observed['sourceAgeVerified'])
        self.assertGreaterEqual(observed['ageSeconds'],0)
        probe.disconnected()
        self.assertIsNone(probe.state()['heartObservation'])

    def test_old_registered_flag_cannot_be_invented_as_a_new_diagnostic(self):
        self.probe.connected=True
        self.probe.remember_heart_observation(dict(heartRunning=True,heartRegistered=True))
        self.assertIsNone(self.probe.state()['heartObservation'])


    def test_radio_off_longer_than_three_attempts_still_reconnects(self):
        probe = self.probe
        probe.config = dict(watchAddress='02:00:00:00:00:01', token='unit-test-only')
        class Client:
            def __init__(self):
                self.data = io.BytesIO(frame(dict(event='hello', protocolVersion=1)))
                self.closed = False
            def sendall(self, data): pass
            def recv(self, size): return self.data.read(size)
            def settimeout(self, timeout): probe.stopping.set()
            def close(self): self.closed = True
        client = Client()
        outcomes = [ConnectionError('peer closed'), *[OSError(10050, 'radio off') for _ in range(4)], client]
        with patch('rfcomm_probe.connect', side_effect=outcomes) as connect_mock, \
             patch.object(probe.stopping, 'wait', return_value=False):
            probe.run(5)
        self.assertEqual(connect_mock.call_count, 6)
        self.assertEqual(probe.connection_serial, 1)
        self.assertTrue(client.closed)
        probe.stream.flush()
        rows = [json.loads(line) for line in Path(probe.stream.name).read_text(encoding='utf-8').splitlines()]
        self.assertEqual(sum(row.get('radioUnavailable') is True for row in rows), 4)
        self.assertFalse(any(row['kind'] == 'attempts_exhausted' for row in rows))

    def test_three_consecutive_other_failures_still_stop(self):
        probe = self.probe
        probe.config = dict(watchAddress='02:00:00:00:00:01', token='unit-test-only')
        with patch('rfcomm_probe.connect', side_effect=TimeoutError('unreachable')) as connect_mock, \
             patch.object(probe.stopping, 'wait', return_value=False):
            probe.run(5)
        self.assertEqual(connect_mock.call_count, 3)
        self.assertEqual(probe.connection_serial, 0)
        self.assertTrue(probe.stopping.is_set())

    def test_daily_mode_survives_more_than_three_absent_watch_failures(self):
        probe=self.probe;probe.config=dict(watchAddress='02:00:00:00:00:01',token='unit-test-only')
        class Client:
            def __init__(self):self.data=io.BytesIO(frame(dict(event='hello',protocolVersion=1)))
            def sendall(self,data):pass
            def recv(self,size):return self.data.read(size)
            def settimeout(self,value):probe.stopping.set()
            def close(self):pass
        with patch('rfcomm_probe.connect',side_effect=[*[OSError(10049,'watch not ready') for _ in range(5)],Client()]) as dial,patch.object(probe.stopping,'wait',return_value=False):
            probe.run(None,persistent=True)
        self.assertEqual(dial.call_count,6);self.assertEqual(probe.connection_serial,1)
        rows=[json.loads(line) for line in Path(probe.stream.name).read_text(encoding='utf-8').splitlines()]
        self.assertFalse(any(row['kind']=='attempts_exhausted' for row in rows))


if __name__ == '__main__': unittest.main()
