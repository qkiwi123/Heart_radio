import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import server


class ConnectionApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.transport=Mock(mode='rfcomm')
        self.transport.state.return_value=dict(connected=False,agentEnabled=False)
        self.transport.control.return_value=dict(connected=False,agentEnabled=False)
        self.access=Mock()
        self.patches=[patch.object(server,'transport',self.transport),patch.object(server,'data_access',self.access)]
        for item in self.patches:item.start();self.addCleanup(item.stop)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.worker=threading.Thread(target=self.http.serve_forever,daemon=True);self.worker.start()
        self.addCleanup(self.http.server_close);self.addCleanup(self.http.shutdown)

    def request(self,method,path,value=None,headers=None):
        client=http.client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=4)
        self.addCleanup(client.close)
        selected={'Content-Type':'application/json','X-HeartRadio-Action':'connection'}
        selected.update(headers or {})
        client.request(method,path,body=json.dumps(value) if value is not None else None,headers=selected)
        response=client.getresponse();return response.status,response.read()

    def test_state_is_local_and_never_creates_a_connection(self):
        status,body=self.request('GET','/api/connection')
        self.assertEqual(status,200);self.assertFalse(json.loads(body)['connected'])
        self.transport.control.assert_not_called();self.access.call.assert_not_called()

    def test_connection_controls_reject_cross_origin_and_non_object_bodies(self):
        self.assertEqual(self.request('POST','/api/connection',dict(action='agent_on'),{'Origin':'https://example.com'})[0],403)
        self.assertEqual(self.request('POST','/api/connection',['agent_on'])[0],400)
        self.transport.control.assert_not_called()

    def test_bluetooth_csv_exports_bluetooth_instead_of_old_wifi_records(self):
        log=Path(self.temporary.name)/'bluetooth.jsonl'
        log.write_text(json.dumps(dict(kind='watch_message',pcReceiptMs=200,message=dict(event='heart',
            watchReceiptMs=100,bpm=81,accuracy=3,valid=True,heartAttemptId='new',sequence=1)))+'\n',encoding='utf-8')
        self.transport.probe.stream.name=str(log)
        status,body=self.request('GET','/api/export.csv')
        self.assertEqual(status,200)
        self.assertIn('100,200,81,3,True,new,1',body.decode('utf-8-sig'))


if __name__=='__main__':unittest.main()
