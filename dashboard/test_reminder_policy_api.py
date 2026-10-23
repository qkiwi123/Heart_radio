import http.client
from http.server import ThreadingHTTPServer
import json
import tempfile
import threading
import unittest
from unittest.mock import patch,Mock
import server
from reminders import Reminders
from transport_access import TransportAccess
from reminder_policy import DEFAULTS


class ReminderPolicyApiTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.transport=TransportAccess(self.folder.name)
        p=patch.object(server,'transport',self.transport);p.start();self.addCleanup(p.stop)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.http.server_close);self.addCleanup(self.http.shutdown)
    def request(self,method,value=None,**headers):
        c=http.client.HTTPConnection('127.0.0.1',self.http.server_port);self.addCleanup(c.close)
        c.request(method,'/api/reminder-policy',body=json.dumps(value) if value is not None else None,
            headers={'Content-Type':'application/json','X-HeartRadio-Action':'reminder-policy',**headers})
        r=c.getresponse();return r.status,r.read()
    def test_default_local_get_and_offline_save_do_not_enable_services(self):
        code,body=self.request('GET');self.assertEqual(code,200);self.assertEqual(json.loads(body)['limits']['maxVibrationMs'],6000)
        code,body=self.request('POST',dict(settings=dict(DEFAULTS,maxVibrationMs=3000,wakeEnabled=False)))
        self.assertEqual(code,200);self.assertFalse(json.loads(body)['peerConfirmed']);self.assertIsNone(self.transport.manager_thread);self.assertFalse(self.transport.agent_enabled)
        restored=TransportAccess(self.folder.name);self.assertFalse(restored.reminder_limits()['wakeEnabled'])
    def test_cross_origin_and_unknown_controls_cannot_change_limits(self):
        for headers in [dict(Origin='https://outside.example'),dict(Host='outside.example'),{'X-HeartRadio-Action':''}]:
            self.assertEqual(self.request('POST',dict(settings=DEFAULTS),**headers)[0],403)
        for value in [dict(action='agent_on'),dict(settings=dict(DEFAULTS,maxVibrationMs=11000)),dict(settings=DEFAULTS,enable=True)]:
            self.assertEqual(self.request('POST',value)[0],400)
        self.assertEqual(self.transport.reminder_limits(),DEFAULTS)
    def test_shared_sender_enforces_default_six_before_wire_and_returns_limit(self):
        wire=Mock();service=Reminders(self.folder.name,wire,policy=self.transport.reminder_limits)
        p=dict(reminderId='too_long',title='t',body='b',ttlMs=60000,vibration=dict(pulseMs=1000,gapMs=1501,count=3))
        result=service.send(p);self.assertEqual(result['status'],'reminder_policy_denied');self.assertEqual(result['limits']['maxVibrationMs'],6000);wire.assert_not_called()
        self.assertEqual(result['policyReason'],'vibration_exceeds_user_limit')
    def test_known_duplicate_is_not_replayed_when_limits_change(self):
        wire=Mock(return_value=dict(status='issued',reminderId='one'))
        service=Reminders(self.folder.name,wire,policy=self.transport.reminder_limits)
        p=dict(reminderId='one',title='t',body='b',ttlMs=60000)
        service.send(p);self.transport.edit_reminder_policy(dict(DEFAULTS,vibrationEnabled=False,wakeEnabled=False))
        self.assertTrue(service.send(p)['duplicate']);wire.assert_called_once()


if __name__=='__main__':unittest.main()
