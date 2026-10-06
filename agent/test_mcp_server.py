import io
import json
import unittest
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
        self.assertEqual(len(value['result']['tools']), 6)
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


if __name__ == '__main__':
    unittest.main()
