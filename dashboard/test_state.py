import unittest
import json
from pathlib import Path
import tempfile
from state import State, LogReader


def row(kind,seconds=0,**extra):
    return dict(kind=kind,pcReceiptMs=1_700_000_000_000+seconds*1000,pcMonotonicNs=seconds*1_000_000_000,**extra)


def packet(kind='sensor_event',seconds=0,valid=True,accuracy=3,session=1):
    return row('packet',seconds,packet=dict(kind=kind,valid=valid,bpm=80 if valid else None,
                accuracy=accuracy,session=session,watchReceiptMs=1_700_000_000_000+seconds*1000),sequenceStep=1)


class DisplayStateTest(unittest.TestCase):
    def setUp(self):
        self.state=State();self.state.consume(row('wifi_connected'))
    def snapshot(self,seconds):
        return self.state.snapshot(seconds*1_000_000_000,1_700_000_000_000+seconds*1000)
    def test_heartbeat_alone_never_creates_measurement(self):
        self.state.consume(packet('heartbeat'))
        s=self.snapshot(1);self.assertIsNone(s['bpm']);self.assertEqual(s['validCount'],0);self.assertEqual(s['points'],[])
    def test_heartbeats_do_not_refresh_old_measurement(self):
        self.state.consume(packet(seconds=1))
        self.state.consume(packet('heartbeat',seconds=32))
        s=self.snapshot(32);self.assertIsNone(s['bpm']);self.assertTrue(s['connected']);self.assertEqual(s['status'],'超过30秒无新心率')
    def test_no_contact_clears_and_accuracy_does_not_resurrect(self):
        self.state.consume(packet(seconds=1))
        self.state.consume(packet(seconds=2,valid=False,accuracy=-1))
        self.state.consume(packet('accuracy',seconds=3))
        self.assertIsNone(self.snapshot(3)['bpm']);self.assertEqual(self.snapshot(3)['validCount'],1)
    def test_disconnect_hides_value_reconnect_requires_fresh_event(self):
        self.state.consume(packet(seconds=1));self.state.consume(row('wifi_disconnected',2))
        self.assertIsNone(self.snapshot(2)['bpm'])
        self.state.consume(row('wifi_connected',3));self.state.consume(packet('heartbeat',4))
        self.assertIsNone(self.snapshot(4)['bpm'])
        self.state.consume(packet(seconds=5));self.assertEqual(self.snapshot(5)['bpm'],80)
    def test_new_watch_session_cannot_reuse_old_value(self):
        self.state.consume(packet(seconds=1));self.state.consume(packet('heartbeat',seconds=2,session=2))
        self.assertIsNone(self.snapshot(2)['bpm'])
    def test_silent_link_is_not_reported_connected(self):
        self.state.consume(packet(seconds=1));s=self.snapshot(22)
        self.assertFalse(s['connected']);self.assertIsNone(s['bpm'])
    def test_pc_wall_clock_adjustment_does_not_expire_live_event(self):
        self.state.consume(packet(seconds=1))
        s=self.state.snapshot(2_000_000_000,1_700_000_300_000)
        self.assertEqual(s['bpm'],80);self.assertEqual(s['eventAgeSeconds'],1)
    def test_stopped_collection_flag_clears_display(self):
        self.state.consume(packet(seconds=1))
        status=packet('heartbeat',seconds=2);status['packet']['running']=False
        self.state.consume(status)
        self.assertIsNone(self.snapshot(2)['bpm'])
    def test_server_stop_stays_stopped(self):
        self.state.consume(packet(seconds=1));self.state.consume(row('server_stop',2))
        s=self.snapshot(10);self.assertIsNone(s['bpm']);self.assertTrue(s['receiverStopped'])
        self.assertEqual(s['endedMs'],1_700_000_002_000)


class LiveLogReaderTest(unittest.TestCase):
    def test_partial_write_is_counted_once_when_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);run=root/'runs'/'one';run.mkdir(parents=True)
            path=run/'pc.jsonl'
            metadata=dict(runId='one',logPath=str(path),host='127.0.0.1',watchAddress='127.0.0.2',durationSeconds=60)
            (root/'runs'/'active.json').write_text(json.dumps(metadata),encoding='utf-8')
            value=json.dumps(packet(seconds=1))+'\n';cut=len(value)//2
            path.write_text(value[:cut],encoding='utf-8')
            reader=LogReader(root)
            self.assertEqual(reader.refresh()['validCount'],0)
            with path.open('a',encoding='utf-8') as stream: stream.write(value[cut:])
            self.assertEqual(reader.refresh()['validCount'],1)
            self.assertEqual(reader.refresh()['validCount'],1)
            self.assertEqual(reader.refresh()['parseErrors'],0)
