import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import Mock, patch
import server
from data_policy import DEFAULTS


class CapabilityApiTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.status.return_value = dict(running=False, state='stopped', enabledOnStart=False)
        self.service.control.return_value = dict(running=True, state='ready')
        self.service.save.return_value = dict(running=False, state='stopped', enabledOnStart=True)
        self.service.duration_settings.return_value = dict(seconds=0,revision='default')
        self.service.save_duration.return_value = dict(seconds=5400,revision='saved')
        override = patch.object(server, 'capability_service', self.service)
        override.start(); self.addCleanup(override.stop)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()

    def request(self, method, path='/api/capability-service', value=None, extra=None):
        client = http.client.HTTPConnection('127.0.0.1', self.http.server_port)
        headers = {'Content-Type': 'application/json', 'X-HeartRadio-Action': path.split('/')[-1]}
        headers.update(extra or {})
        client.request(method, path, body=json.dumps(value) if value is not None else None, headers=headers)
        response = client.getresponse(); status, data = response.status, response.read(); client.close()
        return status, data

    def test_get_only_reads_state_and_settings_are_separate_from_control(self):
        self.assertEqual(self.request('GET')[0], 200)
        self.service.control.assert_not_called(); self.service.save.assert_not_called()
        self.assertEqual(self.request('POST', '/api/capability-settings', dict(enabledOnStart=True))[0], 200)
        self.service.control.assert_not_called()
        self.assertEqual(self.request('POST', value=dict(action='start'))[0], 200)
        self.service.control.assert_called_once_with(dict(action='start'))

    def test_cross_origin_forms_and_wrong_action_headers_cannot_control(self):
        for extra in ({'Origin':'https://outside.example'}, {'Host':'outside.example'}, {'X-HeartRadio-Action':''}, {'Content-Type':'text/plain'}):
            self.assertEqual(self.request('POST', value=dict(action='stop'), extra=extra)[0], 403)
        self.service.control.assert_not_called()

    def test_validation_and_start_failure_are_reported(self):
        self.service.control.side_effect = ValueError('invalid action')
        self.assertEqual(self.request('POST', value=dict(action='shell'))[0], 400)
        self.service.control.side_effect = OSError('occupied port')
        status, body = self.request('POST', value=dict(action='start'))
        self.assertEqual(status, 503); self.assertEqual(json.loads(body)['error'], 'occupied port')

    def test_read_settings_require_same_origin_and_do_not_start_services(self):
        policy = Mock()
        policy.read.return_value = DEFAULTS
        policy.save.return_value = dict(DEFAULTS,measurementSeconds=8)
        with patch.object(server.data_access,'policy',policy):
            status,body=self.request('GET','/api/data-settings')
            self.assertEqual(status,200)
            self.assertEqual(json.loads(body)['measurementSeconds'],4)
            status,body=self.request('POST','/api/data-settings',dict(DEFAULTS,measurementSeconds=8))
            self.assertEqual(status,200)
            self.assertEqual(json.loads(body)['measurementSeconds'],8)
            self.assertEqual(self.request('POST','/api/data-settings',DEFAULTS,{'Origin':'https://outside.example'})[0],403)
        self.service.control.assert_not_called()

    def test_duration_settings_are_separate_and_use_the_same_local_access_checks(self):
        status,body=self.request('GET','/api/capability-duration')
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['seconds'],0)
        request=dict(unlimited=False,amount=1.5,unit='hours')
        self.assertEqual(self.request('POST','/api/capability-duration',request)[0],200)
        self.service.save_duration.assert_called_once_with(request)
        self.assertEqual(self.request('POST','/api/capability-duration',request,{'Origin':'https://outside.example'})[0],403)
        self.service.control.assert_not_called()


if __name__ == '__main__':
    unittest.main()
