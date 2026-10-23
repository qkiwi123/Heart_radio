import io
import json
import unittest
import threading
from unittest.mock import patch
import urllib.error
from mcp_server import Server, run


class MCPTests(unittest.TestCase):
    def ready_server(self, version='2025-06-18'):
        server = Server()
        value = server.dispatch(dict(jsonrpc='2.0', id=1, method='initialize', params=dict(protocolVersion=version)))
        self.assertEqual(value['result']['protocolVersion'], version)
        self.assertIsNone(server.dispatch(dict(jsonrpc='2.0', method='notifications/initialized')))
        return server

    def test_requires_initialization_and_returns_reading_and_cancel_tools(self):
        server = Server()
        self.assertIn('error', server.dispatch(dict(jsonrpc='2.0', id=1, method='tools/list')))
        server = self.ready_server()
        value = server.dispatch(dict(jsonrpc='2.0', id=2, method='tools/list'))
        self.assertEqual(len(value['result']['tools']), 11)
        self.assertIn('cancel_measurement', [tool['name'] for tool in value['result']['tools']])

    def test_structured_result_matches_text_and_request_id(self):
        server = self.ready_server()
        server.invoke = lambda name, args: dict(ok=True, value=73, unit='bpm', sourceAgeVerified=False)
        value = server.dispatch(dict(jsonrpc='2.0', id='hr', method='tools/call', params=dict(name='get_heart_rate', arguments={})))
        self.assertEqual(value['id'], 'hr')
        self.assertEqual(json.loads(value['result']['content'][0]['text']), value['result']['structuredContent'])
        self.assertFalse(value['result']['isError'])

    def test_older_version_uses_text_and_offline_is_explicit(self):
        server = self.ready_server('2024-11-05')
        server.invoke = lambda name, args: dict(ok=False, status='watch_offline')
        value = server.dispatch(dict(jsonrpc='2.0', id=2, method='tools/call', params=dict(name='get_device_status')))
        self.assertTrue(value['result']['isError'])
        self.assertNotIn('structuredContent', value['result'])

    def test_bad_and_oversized_frames_do_not_pollute_stdout(self):
        input_stream = io.BytesIO(b'bad\n' + b'x' * 8193 + b'\n')
        output_stream = io.BytesIO()
        run(input_stream, output_stream, Server())
        messages = [json.loads(line) for line in output_stream.getvalue().splitlines()]
        self.assertEqual([row['error']['code'] for row in messages], [-32700, -32600])

    def test_new_tools_required_and_nested_fields_are_checked_before_http(self):
        server=self.ready_server()
        base=dict(reminder_id='one',title='提醒',body='正文')
        invalid=[{},dict(base,unexpected=True),dict(base,vibration=dict(count=True)),
                 dict(base,vibration=dict(pulse_ms=1001)),dict(base,screen='yes'),dict(base,body='😀'*501)]
        with patch('mcp_server.LOCAL_HTTP.open') as wire:
            for args in invalid:
                response=server.dispatch(dict(jsonrpc='2.0',id=3,method='tools/call',params=dict(name='send_reminder',arguments=args)))
                self.assertEqual(response['error']['code'],-32602)
            wire.assert_not_called()

    def test_failed_send_keeps_id_and_never_retries_local_http(self):
        server=self.ready_server()
        with patch('mcp_server.LOCAL_HTTP.open',side_effect=TimeoutError('test timeout')) as wire:
            response=server.dispatch(dict(jsonrpc='2.0',id=4,method='tools/call',params=dict(name='send_reminder',arguments=dict(reminder_id='keep_me',title='t',body='b'))))
        result=response['result'];self.assertTrue(result['isError']);value=result['structuredContent']
        self.assertEqual(value['status'],'outcome_unknown_local_failure');self.assertEqual(value['reminderId'],'keep_me')
        self.assertFalse(value['automaticallyRetried']);wire.assert_called_once()

    def test_http_server_failure_after_send_is_also_uncertain(self):
        with patch('mcp_server.LOCAL_HTTP.open',side_effect=urllib.error.HTTPError('test',500,'failure',{},None)) as wire:
            result=Server().invoke('send_reminder',dict(reminder_id='keep_me',title='t',body='b'))
        self.assertEqual(result['status'],'outcome_unknown_local_failure');wire.assert_called_once()

    def test_catalog_has_no_permission_enabler_and_explains_effects(self):
        server=self.ready_server();tools=server.dispatch(dict(jsonrpc='2.0',id=2,method='tools/list'))['result']['tools']
        names={t['name'] for t in tools}
        self.assertEqual(names,{'read_watch_data','get_capabilities','get_heart_rate','get_device_status','measure_pressure','measure_motion','cancel_measurement','send_reminder','get_reminder_status','measure_heart_rate','get_user_state'})
        send=next(t for t in tools if t['name']=='send_reminder')
        self.assertFalse(send['annotations']['readOnlyHint']);self.assertIn('reminder_id',send['inputSchema']['required'])

    def test_state_unknown_is_a_valid_result_and_arguments_are_empty(self):
        server=self.ready_server()
        server.invoke=lambda *args:dict(ok=True,status='insufficient_evidence',tier=None,label='暂不判断')
        value=server.dispatch(dict(jsonrpc='2.0',id='state',method='tools/call',params=dict(name='get_user_state',arguments={})))
        self.assertFalse(value['result']['isError'])
        self.assertIsNone(value['result']['structuredContent']['tier'])
        server=self.ready_server()
        with patch('mcp_server.LOCAL_HTTP.open') as wire:
            value=server.dispatch(dict(jsonrpc='2.0',id='state',method='tools/call',params=dict(name='get_user_state',arguments=dict(seconds=10))))
            self.assertEqual(value['error']['code'],-32602)
            wire.assert_not_called()

    def test_new_tool_results_remain_compatible_with_older_protocol(self):
        for version in ('2024-11-05','2025-06-18'):
            server=self.ready_server(version);server.invoke=lambda *args:dict(ok=True,status='acknowledged',reminderId='one')
            response=server.dispatch(dict(jsonrpc='2.0',id='receipt',method='tools/call',params=dict(name='get_reminder_status',arguments=dict(reminder_id='one'))))
            self.assertFalse(response['result']['isError']);self.assertEqual(response['id'],'receipt')
            self.assertEqual('structuredContent' in response['result'],version=='2025-06-18')

    def test_independent_calls_finish_out_of_order_with_their_original_ids(self):
        server=self.ready_server();fast=threading.Event()
        def invoke(name,args):
            if name=='measure_pressure':
                self.assertTrue(fast.wait(2))
            else:fast.set()
            return dict(ok=True,status='ok',tool=name)
        server.invoke=invoke
        frames=[dict(jsonrpc='2.0',id='slow',method='tools/call',params=dict(name='measure_pressure',arguments={})),
                dict(jsonrpc='2.0',id='fast',method='tools/call',params=dict(name='get_device_status',arguments={}))]
        output=io.BytesIO();run(io.BytesIO(''.join(json.dumps(row)+'\n' for row in frames).encode()),output,server)
        rows=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual({row['id'] for row in rows},{'slow','fast'})
        for row in rows:self.assertEqual(row['result']['structuredContent']['tool'],'measure_pressure' if row['id']=='slow' else 'get_device_status')

    def test_cancellation_does_not_wait_behind_eight_active_calls(self):
        server=self.ready_server();started=threading.Barrier(8);release=threading.Event()
        def invoke(name,args):
            if name=='cancel_measurement':release.set()
            else:started.wait(2);self.assertTrue(release.wait(2))
            return dict(ok=True,status='ok')
        server.invoke=invoke
        frames=[dict(jsonrpc='2.0',id=i,method='tools/call',params=dict(name='measure_pressure',arguments={})) for i in range(8)]
        frames.append(dict(jsonrpc='2.0',id=99,method='tools/call',params=dict(name='cancel_measurement',arguments={})))
        output=io.BytesIO();run(io.BytesIO(''.join(json.dumps(row)+'\n' for row in frames).encode()),output,server)
        self.assertEqual({json.loads(line)['id'] for line in output.getvalue().splitlines()},set(range(8))|{99})

    def test_combination_schema_rejects_unknown_or_duplicate_metrics(self):
        server=self.ready_server()
        with patch('mcp_server.LOCAL_HTTP.open') as wire:
            for metrics in ([],['pressure','pressure'],['location'],[True]):
                response=server.dispatch(dict(jsonrpc='2.0',id=5,method='tools/call',params=dict(name='read_watch_data',arguments=dict(metrics=metrics))))
                self.assertEqual(response['error']['code'],-32602)
            wire.assert_not_called()


if __name__ == '__main__':
    unittest.main()
