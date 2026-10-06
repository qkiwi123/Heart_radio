import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import server


class SettingsApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.override = patch.object(server, 'SETTINGS', Path(self.temp.name) / 'settings.json')
        self.override.start()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.worker.join()
        self.override.stop(); self.temp.cleanup()

    def request(self, method, value=None, extra=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port)
        headers = {'Content-Type': 'application/json', 'X-HeartRadio-Action': 'receiver-settings'}
        headers.update(extra or {})
        connection.request(method, '/api/receiver-settings', body=json.dumps(value) if value is not None else None, headers=headers)
        response = connection.getresponse(); status = response.status; body = response.read()
        connection.close()
        return status, body

    def test_default_save_and_reload(self):
        self.assertEqual(json.loads(self.request('GET')[1])['seconds'], 0)
        status, body = self.request('POST', dict(unlimited=False, amount=1.5, unit='hours'))
        self.assertEqual(status, 200)
        saved = json.loads(body)
        self.assertEqual(saved['seconds'], 5400)
        self.assertEqual(json.loads(self.request('GET')[1]), saved)

    def test_reject_cross_origin_and_missing_action(self):
        for extra in ({'Origin':'https://example.com'}, {'X-HeartRadio-Action':''}, {'Host':'example.com'}):
            self.assertEqual(self.request('POST', dict(unlimited=True), extra)[0], 403)
        self.assertFalse(server.SETTINGS.exists())

    def test_invalid_duration_cannot_overwrite_previous(self):
        self.request('POST', dict(unlimited=True))
        status, body = self.request('POST', dict(unlimited=False, amount=-1, unit='minutes'))
        self.assertEqual(status, 400)
        self.assertIn('error', json.loads(body))
        self.assertEqual(json.loads(self.request('GET')[1])['seconds'], 0)
