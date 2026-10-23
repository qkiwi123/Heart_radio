import csv
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
import unittest

from history import BEIJING, HistoryStore, POINT_LIMIT, period


def stamp(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=BEIJING).timestamp()*1000)


def event(value, bpm=80, *, valid=True, kind='sensor_event', sequence=1, session=1):
    timestamp=stamp(value) if isinstance(value,str) else value
    return dict(kind='packet',pcReceiptMs=timestamp,sequenceStep=1,
                packet=dict(kind=kind,bpm=bpm,valid=valid,running=True,registered=True,
                            accuracy=3,watchReceiptMs=timestamp+1000,session=session,sequence=sequence))


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=HistoryStore(self.root)

    def write(self,run,rows,mode='w'):
        path=self.root/'runs'/run/'pc.jsonl';path.parent.mkdir(exist_ok=True)
        with path.open(mode,encoding='utf-8') as stream:
            for row in rows: stream.write(json.dumps(row)+'\n')
        return path

    def query(self,**values):
        return self.store.query({k:str(v) for k,v in values.items()})

    def test_year_month_day_follow_event_time_across_midnight_and_year(self):
        self.write('20261231-235900-001',[event('2026-12-31T23:59:59'),event('2027-01-01T00:00:00',90)])
        self.assertEqual([d['date'] for d in self.store.catalog()['days']],['2027-01-01','2026-12-31'])
        self.assertEqual(self.query(year=2026)['statistics']['validCount'],1)
        self.assertEqual(self.query(year=2027,month=1,day=1)['points'][0]['bpm'],90)
        self.assertEqual(self.query(year=2027,month=1)['buckets'][0]['label'],'2027-01-01')
        self.assertEqual(self.query(year=2027)['buckets'][0]['label'],'2027-01')

    def test_multiple_runs_evidence_copy_excluded_and_run_filter(self):
        run='20260926-120000-001'
        original=self.write(run,[event('2026-09-26T12:00:00',70)])
        self.write('20260926-130000-002',[event('2026-09-26T13:00:00',90)])
        evidence=self.root/'evidence';evidence.mkdir();(evidence/'pc.jsonl').write_bytes(original.read_bytes())
        result=self.query(year=2026,month=9,day=26)
        self.assertEqual(result['statistics']['validCount'],2);self.assertEqual(result['statistics']['runCount'],2)
        self.assertNotEqual(result['points'][0]['segment'],result['points'][1]['segment'])
        selected=self.query(year=2026,month=9,day=26,run=run)
        self.assertEqual(selected['statistics']['validCount'],1);self.assertEqual(len(selected['runs']),2)

    def test_bluetooth_capture_identities_separate_history_segments(self):
        self.write('20261020-120000-001',[event('2026-10-20T12:00:00',session='attempt-a'),event('2026-10-20T12:00:01',session='attempt-b')])
        result=self.query(year=2026,month=10,day=20)
        self.assertEqual(result['statistics']['validCount'],2)
        self.assertNotEqual(result['points'][0]['segment'],result['points'][1]['segment'])

    def test_incremental_partial_lines_restart_and_replacement(self):
        run='20260926-120000-001';path=self.write(run,[event('2026-09-26T12:00:00')])
        self.assertEqual(self.query(year=2026)['statistics']['validCount'],1)
        value=(json.dumps(event('2026-09-26T12:00:01',90))+'\n').encode()
        with path.open('ab') as stream: stream.write(value[:40])
        self.assertEqual(self.query(year=2026)['statistics']['validCount'],1)
        with path.open('ab') as stream: stream.write(value[40:])
        self.assertEqual(self.query(year=2026)['statistics']['validCount'],2)
        self.store=HistoryStore(self.root)
        self.assertEqual(self.query(year=2026)['statistics']['validCount'],2)
        self.write(run,[event('2026-09-27T12:00:00',60)])
        self.assertEqual(self.query(year=2026,month=9,day=26)['statistics']['validCount'],0)
        self.assertEqual(self.query(year=2026,month=9,day=27)['statistics']['minimum'],60)

    def test_status_invalid_values_and_gaps_do_not_become_heart_rate(self):
        rows=[event('2026-09-26T12:00:00',70),event('2026-09-26T12:00:10',kind='heartbeat'),
              event('2026-09-26T12:00:20',kind='heartbeat'),event('2026-09-26T12:00:40',80),
              event('2026-09-26T12:00:41',None,valid=False,kind='heartbeat'),
              event('2026-09-26T12:00:42',90),event('2026-09-26T12:00:43',0),
              event('2026-09-26T12:00:44',float('nan'))]
        self.write('20260926-120000-001',rows)
        result=self.query(year=2026,month=9,day=26)
        self.assertEqual(result['statistics']['validCount'],3);self.assertEqual(result['statistics']['eventCount'],5)
        self.assertEqual(len({p['segment'] for p in result['points']}),3)
        self.assertEqual(result['statistics']['minimum'],70)

    def test_high_volume_plot_is_bounded_but_statistics_and_csv_are_complete(self):
        start=stamp('2026-09-26T00:00:00')
        rows=[event(start+i*1000,50 if i==1234 else 180 if i==1235 else 80,sequence=i) for i in range(8100)]
        self.write('20260926-000000-001',rows)
        result=self.query(year=2026,month=9,day=26)
        self.assertTrue(result['downsampled']);self.assertLessEqual(len(result['points']),POINT_LIMIT)
        self.assertEqual(result['statistics']['validCount'],8100)
        self.assertEqual(min(p['bpm'] for p in result['points']),50)
        self.assertEqual(max(p['bpm'] for p in result['points']),180)
        data=b''.join(self.store.csv_chunks({'year':'2026','month':'9','day':'26'})).decode('utf-8-sig')
        self.assertEqual(len(list(csv.DictReader(io.StringIO(data)))),8100)

    def test_heartbeat_only_day_is_visible_without_fake_measurements(self):
        self.write('20260926-120000-001',[event('2026-09-26T12:00:00',kind='heartbeat')])
        self.assertEqual(self.store.catalog()['days'][0]['validCount'],0)
        result=self.query(year=2026,month=9,day=26)
        self.assertEqual(result['statistics']['runCount'],1);self.assertEqual(result['points'],[])
        self.assertIsNone(result['statistics']['minimum'])

    def test_bad_line_reports_warning_and_does_not_connect_across_it(self):
        path=self.write('20260926-120000-001',[event('2026-09-26T12:00:00')])
        with path.open('a',encoding='utf-8') as stream: stream.write('{bad json}\n')
        self.write('20260926-120000-001',[event('2026-09-26T12:00:01')],'a')
        result=self.query(year=2026,month=9,day=26)
        self.assertTrue(result['warnings']);self.assertNotEqual(result['points'][0]['segment'],result['points'][1]['segment'])

    def test_invalid_dates_paths_and_unknown_query_fields_rejected(self):
        for values in [{},{'year':'2026','day':'1'},{'year':'2026','month':'2','day':'30'},
                       {'year':'2026','month':'0'}, {'year':'2026','month':'1','day':'0'},
                       {'year':'2026','run':'../../evidence'},{'year':'2026','path':'secret'}, {'year':'9999'}]:
            with self.subTest(values=values), self.assertRaises(ValueError): period(values)
        with self.assertRaises(ValueError): self.query(year=2026,run='20260926-000000-000')

    def test_deleted_source_drops_only_rebuildable_index_rows(self):
        path=self.write('20260926-120000-001',[event('2026-09-26T12:00:00')])
        self.assertEqual(self.query(year=2026)['statistics']['validCount'],1)
        path.unlink()
        self.assertEqual(self.store.catalog()['days'],[])
