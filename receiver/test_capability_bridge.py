"""Protocol and request bounds; these tests never count as real sensor evidence."""
import asyncio
import json
import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

from capability_bridge import Bridge, Handler, frame, read_frame, validate_request
from control import Deadline


class FakeWriter:
    def __init__(self, reader=None):
        self.reader = reader
        self.rows = []
        self.closed = False

    def write(self, value): self.rows.append(value)
    async def drain(self): pass
    def close(self):
        self.closed = True
        if self.reader: self.reader.feed_eof()
    async def wait_closed(self): pass
    def get_extra_info(self, name):
        return ('10.0.0.2', 123) if name == 'peername' else self
    def version(self): return 'TEST_TLS'


async def until(predicate):
    for _ in range(100):
        if predicate(): return
        await asyncio.sleep(.01)
    raise AssertionError('Test condition did not complete')


class LocalEndpointTests(unittest.TestCase):
    def test_heart_sample_bound_is_shared_by_wifi(self):
        value=dict(op='heart_sample',params=dict(sensorId=0,seconds=15))
        self.assertEqual(validate_request(value),value)
        for params in [dict(sensorId=0,seconds=16),dict(sensorId=0,seconds=True),dict(sensorId=0,seconds=15,continuous=True)]:
            with self.assertRaises(ValueError):validate_request(dict(op='heart_sample',params=params))
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()
        self.bridge = Bridge(b'x' * 32, Path(self.directory.name) / 'log.jsonl')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.bridge, self.server.loop = self.bridge, self.loop
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.loop.call_soon_threadsafe(self.loop.stop); self.loop_thread.join(); self.loop.close()
        self.bridge.log_file.close(); self.directory.cleanup()

    def call(self, method, path, body=None, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse(); data = response.read(); status = response.status
        client.close()
        return status, data

    def test_state_read_is_local_and_does_not_wake_offline_watch(self):
        status, data = self.call('GET', '/state')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['status'], 'watch_offline')
        self.assertEqual(self.bridge.pending, {})
        self.assertEqual(self.call('GET', '/state', headers={'Origin': 'https://outside.example'})[0], 403)
        self.assertEqual(self.call('GET', '/state', headers={'Host': 'outside.example'})[0], 403)

    def test_control_requires_local_header_and_rejects_arbitrary_commands(self):
        payload = json.dumps(dict(action='stop_watch'))
        headers = {'Content-Type': 'application/json', 'X-HeartRadio-Probe': 'request'}
        self.assertEqual(self.call('POST', '/control', payload, {'Content-Type': 'application/json'})[0], 403)
        self.assertEqual(self.call('POST', '/control', payload, dict(headers, Origin='https://outside.example'))[0], 403)
        self.assertEqual(self.call('POST', '/control', json.dumps(dict(action='shell')), headers)[0], 400)
        status, data = self.call('POST', '/control', payload, headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['status'], 'watch_offline')
        self.assertEqual(self.bridge.pending, {})

    def test_shared_gate_sync_uses_restricted_validated_control_channel(self):
        from interaction_sync import InteractionRegister
        register=InteractionRegister('pc');register.edit(True,30000)
        params=dict(selectionId='a'*32,syncId='b'*32,state=register.wire())
        payload=dict(op='sync_interaction',params=params)
        self.assertEqual(validate_request(payload),payload)
        headers={'Content-Type':'application/json','X-HeartRadio-Probe':'request'}
        code,body=self.call('POST','/request',json.dumps(payload),headers)
        self.assertEqual(code,200);self.assertEqual(json.loads(body)['status'],'watch_offline')
        self.assertEqual(self.call('POST','/request',json.dumps(payload),dict(headers,Origin='https://outside.example'))[0],403)
        for bad in [dict(params,force=True),dict(params,syncId='bad'),dict(params,state={'pc':None})]:
            self.assertEqual(self.call('POST','/request',json.dumps(dict(op='sync_interaction',params=bad)),headers)[0],400)


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_uncertain_reminder_timeout_is_not_replayed_or_sent_as_sensor_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge=Bridge(b'x'*32,Path(directory)/'log.jsonl');writer=FakeWriter();bridge.writer=writer
            try:
                with patch('capability_bridge.asyncio.wait_for',side_effect=asyncio.TimeoutError):
                    result=await bridge.request(dict(op='remind',params=dict(reminderId='once',title='测试',body='仅发一次',ttlMs=60000)))
                self.assertEqual(result['status'],'outcome_unknown_timeout');self.assertEqual(len(writer.rows),1)
                self.assertEqual(json.loads(writer.rows[0][4:])['op'],'remind');self.assertEqual(bridge.pending,{})
            finally:bridge.log_file.close()
    async def test_duration_sync_is_opt_in_and_old_firmware_receives_no_command(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge=Bridge(b'x'*32,Path(directory)/'log.jsonl');writer=FakeWriter();bridge.writer=writer
            bridge.duration_limit=Deadline(60)
            for report in ({},{'followComputer':True},{'durationSyncVersion':1,'followComputer':False}):
                bridge.watch_lifecycle=report;await bridge.sync_duration()
            self.assertEqual(writer.rows,[])
            self.assertEqual(bridge.pending,{})
            bridge.log_file.close()

    async def test_duration_sync_sends_remaining_and_same_connection_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge=Bridge(b'x'*32,Path(directory)/'log.jsonl');writer=FakeWriter();bridge.writer=writer
            bridge.duration_limit=Deadline(60);bridge.duration_limit.ends-=20
            bridge.watch_lifecycle={'followComputer':True,'durationSyncVersion':1}
            await bridge.sync_duration();await bridge.sync_duration()
            self.assertEqual(len(writer.rows),1)
            command=json.loads(writer.rows[0][4:]);self.assertEqual(command['op'],'set_standby_duration')
            self.assertEqual(command['params']['seconds'],60)
            self.assertLessEqual(command['params']['remainingMs'],40000)
            bridge.connection_serial+=1;await bridge.sync_duration()
            self.assertEqual(len(writer.rows),2)
            self.assertLessEqual(json.loads(writer.rows[1][4:])['params']['remainingMs'],command['params']['remainingMs'])
            bridge.log_file.close()

    async def test_new_revision_and_unlimited_sync_require_watch_acknowledgement(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge=Bridge(b'x'*32,Path(directory)/'log.jsonl');writer=FakeWriter();bridge.writer=writer
            bridge.duration_limit=Deadline(60)
            bridge.watch_lifecycle={'followComputer':True,'durationSyncVersion':1}
            await bridge.sync_duration();self.assertFalse(bridge.sync_state()['applied'])
            bridge.watch_lifecycle.update(durationSyncRevision='command-line',durationSyncInstanceId=bridge.instance_id)
            self.assertTrue(bridge.sync_state()['applied'])
            bridge.duration_limit=Deadline(0,dict(revision='new-revision'))
            await bridge.sync_duration();self.assertFalse(bridge.sync_state()['applied'])
            self.assertIsNone(json.loads(writer.rows[-1][4:])['params']['remainingMs'])
            bridge.watch_lifecycle['followComputer']=False;await bridge.sync_duration()
            self.assertFalse(bridge.sync_state()['applied'])
            bridge.log_file.close()

    def test_public_tools_cannot_enable_follow_or_set_duration(self):
        with self.assertRaises(ValueError):validate_request(dict(op='set_standby_duration',params={}))

    async def test_cancel_is_targeted_and_cannot_cancel_a_finished_or_different_request(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(b'x'*32, Path(directory)/'log.jsonl')
            writer = FakeWriter();bridge.writer = writer
            self.assertEqual((await bridge.request(dict(op='cancel',params=dict(targetId='a'*32))))['status'],'not_active')
            self.assertEqual(writer.rows,[])
            sample = asyncio.create_task(bridge.request(dict(op='sample',params=dict(sensorId=1,seconds=10),requestId='a'*32)))
            await until(lambda:len(writer.rows)==1)
            self.assertEqual(json.loads(writer.rows[0][4:])['id'],'a'*32)
            self.assertEqual((await bridge.request(dict(op='sample',params=dict(sensorId=1),requestId='a'*32)))['status'],'duplicate_request')
            cancel = asyncio.create_task(bridge.request(dict(op='cancel',params=dict(targetId='a'*32))))
            await until(lambda:len(writer.rows)==2)
            cancel_id=json.loads(writer.rows[1][4:])['id']
            bridge.pending['a'*32].set_result(dict(id='a'*32,status='cancelled',listenersReleased=True))
            bridge.pending[cancel_id].set_result(dict(id=cancel_id,status='ok'))
            self.assertEqual((await sample)['status'],'cancelled')
            self.assertEqual((await cancel)['status'],'ok')
            self.assertEqual((await bridge.request(dict(op='cancel',params=dict(targetId='a'*32))))['status'],'not_active')
            self.assertEqual(bridge.pending,{})
            self.assertEqual(bridge.pending_operations,{})
            bridge.log_file.close()

    def test_pc_deadline_uses_monotonic_time_not_wall_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(b'x'*32, Path(directory) / 'log.jsonl')
            bridge.deadline = 100
            with patch('capability_bridge.time.monotonic', return_value=75), patch('capability_bridge.time.time_ns', return_value=0):
                self.assertEqual(bridge.state()['serverRemainingSeconds'], 25)
            with patch('capability_bridge.time.monotonic', return_value=105):
                self.assertEqual(bridge.state()['serverRemainingSeconds'], 0)
            bridge.log_file.close()

    def test_location_budget_is_separate_from_sensor_budget(self):
        self.assertEqual(validate_request(dict(op='locate', params=dict(seconds=180)))['params']['seconds'], 180)
        for invalid in (dict(op='locate', params=dict(seconds=181)),
                        dict(op='sample', params=dict(sensorId=1, seconds=61))):
            with self.assertRaises(ValueError): validate_request(invalid)

    async def test_fragmented_utf8_frame(self):
        reader = asyncio.StreamReader()
        payload = frame(dict(status='ok', name='气压'))
        for byte in payload:
            reader.feed_data(bytes([byte]))
        self.assertEqual(await read_frame(reader), dict(status='ok', name='气压'))

    async def test_oversized_response_rejected_before_reading_body(self):
        reader = asyncio.StreamReader(); reader.feed_data(struct.pack('!I', 65537))
        with self.assertRaises(ValueError):
            await read_frame(reader)

    async def test_offline_request_never_queues_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(b'x' * 32, Path(directory) / 'log.jsonl')
            self.assertEqual(await bridge.request(dict(op='status')), dict(status='watch_offline'))
            self.assertEqual(bridge.pending, {})
            bridge.log_file.close()

    def test_bounded_allowlisted_operations(self):
        for invalid in [dict(op='shell'), dict(op='sample', params=dict(sensorId=1, seconds=61)),
                        dict(op='sample', params=dict(sensorId=True)), dict(op='status', params=dict(token='x')),
                        dict(op='locate', params=dict(seconds=0)), dict(op='sample', params=dict(sensorId=1, periodUs=1))]:
            with self.assertRaises(ValueError):
                validate_request(invalid)
        self.assertEqual(validate_request(dict(op='sample', params=dict(sensorId=1, seconds=4)))['op'], 'sample')

    def test_nan_is_not_transmitted_as_valid_json(self):
        with self.assertRaises(ValueError):
            frame(dict(value=float('nan')))

    async def test_disconnect_fails_old_request_and_new_connection_accepts_only_new_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(b'x' * 32, Path(directory) / 'log.jsonl')
            reader = asyncio.StreamReader(); reader.feed_data(b'x' * 32)
            writer = FakeWriter(reader)
            task = asyncio.create_task(bridge.connected(reader, writer))
            await until(lambda: bridge.writer is writer)
            old = asyncio.create_task(bridge.request(dict(op='sample', params=dict(sensorId=1, seconds=4))))
            await until(lambda: len(writer.rows) == 2)
            old_id = next(iter(bridge.pending))
            writer.close()
            self.assertEqual((await old)['status'], 'disconnected')
            await task
            self.assertEqual(bridge.pending, {})
            second_reader = asyncio.StreamReader(); second_reader.feed_data(b'x' * 32)
            second_writer = FakeWriter(second_reader)
            second_task = asyncio.create_task(bridge.connected(second_reader, second_writer))
            await until(lambda: bridge.writer is second_writer)
            second_reader.feed_data(frame(dict(id=old_id, status='data_received')))
            fresh = asyncio.create_task(bridge.request(dict(op='status')))
            await until(lambda: len(second_writer.rows) == 2)
            new_id = next(iter(bridge.pending))
            self.assertNotEqual(old_id, new_id)
            second_reader.feed_data(frame(dict(id=new_id, status='ok')))
            self.assertEqual((await fresh)['status'], 'ok')
            self.assertEqual(bridge.state()['connectionSerial'], 2)
            self.assertEqual(len(second_writer.rows), 2)
            second_writer.close(); await second_task
            bridge.log_file.close()

    async def test_writer_changed_while_waiting_does_not_transmit_old_request(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(b'x' * 32, Path(directory) / 'log.jsonl')
            first, second = FakeWriter(), FakeWriter()
            bridge.writer = first
            await bridge.write_lock.acquire()
            task = asyncio.create_task(bridge.send(dict(id='old'), first))
            await asyncio.sleep(0)
            bridge.writer = second; bridge.write_lock.release()
            with self.assertRaises(ConnectionError): await task
            self.assertEqual(second.rows, [])
            bridge.log_file.close()

    async def test_explicit_stop_frame_is_distinct_from_test_disconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(b'x' * 32, Path(directory) / 'log.jsonl')
            writer = FakeWriter(); bridge.writer = writer
            self.assertEqual((await bridge.control('stop_watch'))['status'], 'stop_sent')
            self.assertEqual(json.loads(writer.rows[0][4:])['op'], 'stop_bridge')
            count = len(writer.rows)
            self.assertEqual((await bridge.control('disconnect'))['status'], 'disconnect_requested')
            self.assertEqual(len(writer.rows), count)
            self.assertTrue(writer.closed)
            with self.assertRaises(ValueError): await bridge.control('arbitrary')
            bridge.log_file.close()

    async def test_session_metadata_has_no_pending_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(b'x' * 32, Path(directory) / 'log.jsonl')
            reader = asyncio.StreamReader(); reader.feed_data(b'x' * 32)
            reader.feed_data(frame(dict(id='bridge_session', status='session', state='connected', leaseRemainingMs=10000)))
            writer = FakeWriter(reader)
            task = asyncio.create_task(bridge.connected(reader, writer))
            await until(lambda: bridge.watch_lifecycle is not None)
            self.assertEqual(bridge.state()['watchLifecycle']['leaseRemainingMs'], 10000)
            self.assertEqual(bridge.pending, {})
            writer.close(); await task
            self.assertFalse(bridge.state()['connected'])
            bridge.log_file.close()

    async def test_bad_token_is_explicit_rejection_not_transient_eof(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(b'x' * 32, Path(directory) / 'log.jsonl')
            reader = asyncio.StreamReader(); reader.feed_data(b'y' * 32)
            writer = FakeWriter(reader)
            await bridge.connected(reader, writer)
            self.assertEqual(writer.rows, [b'\x00'])
            self.assertIsNone(bridge.writer)
            self.assertEqual(bridge.pending, {})
            bridge.log_file.close()


if __name__ == '__main__':
    unittest.main()
