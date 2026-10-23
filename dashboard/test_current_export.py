import csv
import http.client
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
from current_export import csv_chunks
import server


def row(index):
    return dict(kind='packet',pcReceiptMs=1000+index,packet=dict(kind='sensor_event',watchReceiptMs=900+index,
        bpm=70+index%10,accuracy=3,valid=index%7!=0,session='session',sequence=index))


class ExportTests(unittest.TestCase):
    def test_streamed_csv_contains_each_valid_and_invalid_event_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'pc.jsonl'
            path.write_text(''.join(json.dumps(row(i))+'\n' for i in range(4000))+'{partial',encoding='utf-8')
            with patch.object(Path,'read_text',side_effect=AssertionError('whole-file read')):
                chunks=list(csv_chunks(path))
            self.assertGreater(len(chunks),1);self.assertLess(max(map(len,chunks)),18000)
            records=list(csv.reader(io.StringIO(b''.join(chunks).decode('utf-8-sig'))))
            self.assertEqual(len(records),4001);self.assertEqual(records[-1][-1],'3999')
            self.assertEqual(records[1][4],'False')

    def test_http_bluetooth_export_uses_normalized_history_not_diagnostic_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'runs/run/pc.jsonl';path.parent.mkdir(parents=True)
            path.write_text(json.dumps(row(1))+'\n',encoding='utf-8')
            access=Mock(managed=True,mode='rfcomm',run_id='run',root=root)
            access.probe.stream.name=str(root/'missing-diagnostic.jsonl')
            with patch.object(server,'transport',access):
                httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
                worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
                try:
                    conn=http.client.HTTPConnection('127.0.0.1',httpd.server_port)
                    conn.request('GET','/api/export.csv');response=conn.getresponse()
                    data=response.read().decode('utf-8-sig');conn.close()
                    self.assertEqual(response.status,200);self.assertEqual(len(list(csv.reader(io.StringIO(data)))),2)
                finally:httpd.shutdown();httpd.server_close();worker.join()
