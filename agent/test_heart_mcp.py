"""Real stdio -> temporary local HTTP -> simulated heart sensor. No hardware is used."""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import server as dashboard
from data_access import DataAccess


class HeartMCPTests(unittest.TestCase):
    def exercise(self,status,version):
        with tempfile.TemporaryDirectory() as folder:
            inventory=dict(status='ok',model='OW20W2',sdk=27,firmware='OW20W2_11_A.92',bodySensorsPermission=True,
                           sensors=[dict(sensorId=0,type=21,name='HR',stringType='android.sensor.heart_rate')])
            reply=dict(status=status,sensor=dict(type=21,name='HR'),samples=[dict(values=[73],accuracy=3,eventNs=1,watchReceiptWallMs=2)] if status=='data_received' else [],
                       callbacks=1 if status=='data_received' else 0,listenersReleased=True,requestWakeLockHeldAtResult=False,
                       continuousHeartPreserved=False,heartSensorOwnersAfter=0)
            wire=Mock(side_effect=lambda op,params=None,request_id=None:inventory if op=='inventory' else dict(reply,id=request_id))
            access=DataAccess(folder,lambda:dict(connected=True,valid=False,bpm=None),wire)
            with patch.object(dashboard,'data_access',access):
                http=ThreadingHTTPServer(('127.0.0.1',0),dashboard.Handler)
                worker=threading.Thread(target=http.serve_forever,daemon=True);worker.start()
                try:
                    rows=[dict(jsonrpc='2.0',id=1,method='initialize',params=dict(protocolVersion=version)),
                          dict(jsonrpc='2.0',method='notifications/initialized'),dict(jsonrpc='2.0',id=2,method='tools/list'),
                          dict(jsonrpc='2.0',id=3,method='tools/call',params=dict(name='measure_heart_rate',arguments={})),
                          dict(jsonrpc='2.0',id=4,method='tools/call',params=dict(name='get_heart_rate',arguments={}))]
                    process=subprocess.Popen([sys.executable,'-u',str(Path(__file__).with_name('mcp_server.py')),'--port',str(http.server_port)],
                        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
                    try:
                        process.stdin.write(''.join(json.dumps(row)+'\n' for row in rows[:-1]).encode());process.stdin.flush()
                        messages=[json.loads(process.stdout.readline()) for _ in range(3)]
                        # This read depends on the preceding measurement: await its reply.
                        process.stdin.write((json.dumps(rows[-1])+'\n').encode());process.stdin.flush();process.stdin.close()
                        messages.extend(json.loads(line) for line in process.stdout)
                        self.assertEqual(process.wait(timeout=10),0,process.stderr.read().decode(errors='replace'))
                    finally:
                        if process.poll() is None:process.kill();process.wait(timeout=5)
                        process.stdout.close();process.stderr.close()
                    self.assertEqual([row['id'] for row in messages],[1,2,3,4])
                    self.assertEqual(len(messages[1]['result']['tools']),11)
                    measured=messages[2]['result'];value=json.loads(measured['content'][0]['text'])
                    self.assertEqual('structuredContent' in measured,version=='2025-06-18')
                    self.assertEqual(wire.call_count,2);self.assertEqual(wire.call_args.args,('heart_sample',dict(sensorId=0,seconds=15)))
                    cached=json.loads(messages[3]['result']['content'][0]['text'])
                    return value,cached
                finally:http.shutdown();http.server_close();worker.join()

    def test_first_valid_result_then_local_read_through_stdio(self):
        value,cached=self.exercise('data_received','2025-06-18')
        self.assertTrue(value['ok']);self.assertEqual(value['value'],73)
        self.assertFalse(value['continuousHeartPreserved']);self.assertFalse(value['sourceAgeVerified'])
        self.assertEqual(cached['value'],73);self.assertTrue(cached['cached'])

    def test_timeout_without_retry_through_older_stdio_protocol(self):
        value,cached=self.exercise('timeout','2024-11-05')
        self.assertFalse(value['ok']);self.assertEqual(value['status'],'timeout');self.assertIsNone(value['value'])
        self.assertIsNone(cached['value'])
