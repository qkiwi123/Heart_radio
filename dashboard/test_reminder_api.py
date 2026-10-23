import http.client
from http.server import ThreadingHTTPServer
import json
import re
import threading
import unittest
from unittest.mock import Mock,patch
import server


class ReminderApiTests(unittest.TestCase):
    def setUp(self):
        self.reminders=Mock();self.reminders.state.return_value=dict(templates={},reminders=[])
        self.reminders.send.return_value=dict(status='issued',reminderId='test')
        self.reminders.status.return_value=dict(status='interaction_disabled')
        p=patch.object(server,'reminders',self.reminders);p.start();self.addCleanup(p.stop)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.http.server_close);self.addCleanup(self.http.shutdown)
    def call(self,method,body=None,**headers):
        c=http.client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=3);self.addCleanup(c.close)
        h={'Content-Type':'application/json','X-HeartRadio-Action':'reminders',**headers}
        c.request(method,'/api/reminders',body=json.dumps(body) if body is not None else None,headers=h)
        r=c.getresponse();return r.status,r.read()
    def test_local_catalog_does_not_ask_watch(self):
        self.assertEqual(self.call('GET')[0],200);self.reminders.send.assert_not_called();self.reminders.status.assert_not_called()
    def test_every_script_referenced_by_dashboard_is_served_as_javascript(self):
        scripts=re.findall(r'<script src="([^"]+)"', (server.STATIC/'index.html').read_text(encoding='utf-8'))
        self.assertIn('/reminders.js',scripts)
        for path in scripts:
            with self.subTest(path=path):
                c=http.client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=3)
                try:
                    c.request('GET',path);response=c.getresponse()
                    self.assertEqual(response.status,200)
                    self.assertTrue(response.getheader('Content-Type').startswith('text/javascript'))
                    self.assertTrue(response.read())
                finally:c.close()
    def test_send_accepts_unicode_body_and_uses_shared_service(self):
        value=dict(title='标题',body='字'*500)
        self.assertEqual(self.call('POST',dict(action='send',reminder=value))[0],200)
        self.reminders.send.assert_called_once_with(value)
    def test_cross_origin_unknown_action_and_implicit_enable_are_rejected(self):
        self.assertEqual(self.call('POST',dict(action='send',reminder={}),Origin='https://outside.example')[0],403)
        for value in [dict(action='agent_on'),dict(action='send',reminder={},enable=True),[]]:self.assertEqual(self.call('POST',value)[0],400)
        self.reminders.send.assert_not_called()
    def test_device_refresh_is_explicit_and_can_be_rejected_by_gate(self):
        code,body=self.call('POST',dict(action='status',reminderId='same',fresh=True))
        self.assertEqual(code,200);self.assertEqual(json.loads(body)['status'],'interaction_disabled');self.reminders.status.assert_called_once_with('same',True)

    def test_history_is_local_paginated_and_has_no_watch_side_effect(self):
        self.reminders.history.return_value=dict(reminders=[],total=0,capacity=128,page=2,pages=2)
        c=http.client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=3);self.addCleanup(c.close)
        c.request('GET','/api/reminders/history?page=2&q=%E6%B5%8B%E8%AF%95&status=deleted')
        response=c.getresponse();value=json.loads(response.read())
        self.assertEqual(response.status,200);self.assertEqual(value['capacity'],128);self.assertIn('sync',value)
        self.reminders.history.assert_called_once_with(dict(page='2',q='测试',status='deleted'))
        self.reminders.send.assert_not_called();self.reminders.status.assert_not_called()

    def test_repeated_history_parameter_is_bad_request(self):
        c=http.client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=3);self.addCleanup(c.close)
        c.request('GET','/api/reminders/history?page=1&page=2')
        response=c.getresponse();response.read();self.assertEqual(response.status,400)
        self.reminders.history.assert_not_called()


if __name__=='__main__':unittest.main()
