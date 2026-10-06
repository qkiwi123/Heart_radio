import http.client
from http.server import ThreadingHTTPServer
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
import server
from data_access import DataAccess


class ToolsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        state = dict(nowMs=100000, connected=True, valid=True, bpm=71, eventAgeSeconds=1)
        access = DataAccess(self.temp.name, lambda: state, lambda *args: dict(status='watch_offline'))
        override = patch.object(server, 'data_access', access)
        override.start()
        self.addCleanup(override.stop)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.worker.join()

    def request(self, method, path='/api/tools', value=None, extra=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port)
        headers = {'Content-Type': 'application/json', 'X-HeartRadio-Action': 'tools'}
        headers.update(extra or {})
        connection.request(method, path, body=json.dumps(value) if value is not None else None, headers=headers)
        response = connection.getresponse()
        status, body = response.status, response.read()
        connection.close()
        return status, body

    def test_hr_tool_and_cache_get_work_without_watch_bridge(self):
        status, body = self.request('POST', value=dict(name='get_heart_rate', arguments={}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['value'], 71)
        self.assertEqual(self.request('GET', '/api/data')[0], 200)

    def test_cross_origin_and_invalid_tool_args_rejected(self):
        for extra in ({'Origin': 'https://example.com'}, {'Host': 'example.com'}, {'X-HeartRadio-Action': ''}):
            self.assertEqual(self.request('POST', value=dict(name='measure_pressure', arguments={}), extra=extra)[0], 403)
        self.assertEqual(self.request('POST', value=dict(name='measure_pressure', arguments=dict(seconds=60)))[0], 400)
        self.assertEqual(self.request('POST', value=dict(name='vendor_ppg', arguments={}))[0], 400)


if __name__ == '__main__':
    unittest.main()
