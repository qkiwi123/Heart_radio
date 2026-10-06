import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from resource_data import Activity, receiver_path, Targets
from resource_native import iso_filetime
from resource_report import summarize, generate, read_rows


def process(cpu=0,pid=1,creation=1):
    return dict(pid=pid,creationTicks=creation,group='receiver',cpuSeconds=cpu,
                workingSetBytes=20*2**20,privateCommitBytes=10*2**20,threads=2,handles=30)


def sample(elapsed,cpu=0,creation=1):
    return dict(elapsedSeconds=elapsed,processes=[process(cpu,creation=creation)],issues=[],
                activity=dict(packets=1,sensorEvents=1,validEvents=1,freshValid=True))


class ReportTest(unittest.TestCase):
    def setUp(self): self.conditions=dict(logicalProcessors=20,intervalSeconds=5,durationSeconds=60,startedUtc='test')

    def summary(self, samples): return summarize(samples,self.conditions,dict(state='completed'),[])

    def test_cpu_weighted_by_elapsed_and_not_single_core(self):
        result=self.summary([sample(0),sample(5,1),sample(15,2)])
        self.assertAlmostEqual(result['groups']['receiver']['cpuPercentAverage'],100*2/15/20)
        self.assertEqual(result['groups']['receiver']['cpuPercentMaxSample'],1)
        self.assertEqual(result['activity']['validEvents'],2)

    def test_pid_reuse_excludes_cpu_interval(self):
        result=self.summary([sample(0,100),sample(5,.1,creation=2)])
        self.assertIsNone(result['groups']['total']['cpuPercentAverage'])
        self.assertEqual(result['groups']['total']['cpuExcludedIntervals'],1)

    def test_sleep_gap_is_not_reported_as_low_cpu(self):
        result=self.summary([sample(0),sample(5,1),sample(1800,1.1)])
        self.assertEqual(len(result['gaps']),1)
        self.assertEqual(result['observedIntervalSeconds'],5)
        self.assertEqual(result['groups']['receiver']['cpuPercentAverage'],1)

    def test_process_disappears_without_fake_zero(self):
        final=sample(5);final['processes']=[]
        result=self.summary([sample(0),final])
        self.assertIsNone(result['groups']['receiver']['cpuPercentAverage'])
        self.assertEqual(result['groups']['receiver']['workingSetMiBEnd'],20)

    def test_monitor_separate_from_project_sum(self):
        rows=[sample(0),sample(5,1)]
        for row in rows:
            row['processes'].append(dict(process(row['elapsedSeconds']/5,pid=2),group='monitor'))
        result=self.summary(rows)
        self.assertEqual(result['groups']['total']['cpuPercentAverage'],1)
        self.assertEqual(result['groups']['monitor']['cpuPercentAverage'],1)

    def test_unreadable_group_does_not_hide_monitor_cpu(self):
        rows=[sample(0),sample(5,1)]
        for row in rows:
            row['processes'].append(dict(process(row['elapsedSeconds']/5,pid=2),group='monitor'))
        rows[-1]['issues']=[dict(group='receiver',error='cannot read')]
        result=self.summary(rows)
        self.assertIsNone(result['groups']['total']['cpuPercentAverage'])
        self.assertEqual(result['groups']['monitor']['cpuPercentAverage'],1)

    def test_optional_components_count_and_unreadable_mcp_excludes_total(self):
        rows=[sample(0),sample(5,1)]
        for row in rows:
            row['processes'] += [dict(process(row['elapsedSeconds']/10,pid=2),group='capability_bridge'),
                                 dict(process(row['elapsedSeconds']/20,pid=3),group='mcp_adapter')]
        result=self.summary(rows)
        self.assertAlmostEqual(result['groups']['total']['cpuPercentAverage'],1.75)
        self.assertAlmostEqual(result['groups']['mcp_adapter']['cpuPercentAverage'],.25)
        rows[-1]['issues']=[dict(group='mcp_adapter',error='cannot read')]
        self.assertIsNone(self.summary(rows)['groups']['total']['cpuPercentAverage'])

    def test_partial_final_line_recoverable_as_incomplete(self):
        with tempfile.TemporaryDirectory() as name:
            directory=Path(name)
            (directory/'conditions.json').write_text(json.dumps(self.conditions))
            (directory/'status.json').write_text('{"state":"running"}')
            (directory/'samples.jsonl').write_text(json.dumps(sample(0))+'\n'+json.dumps(sample(5,1))+'\n{"broken')
            result=generate(directory,'interrupted')
            self.assertFalse(result['complete']);self.assertEqual(result['invalidLines'],1)
            self.assertEqual(result['samples'],2)
            self.assertIn('中断', (directory/'report.md').read_text(encoding='utf-8'))


class ActivityTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.log=self.root/'runs'/'one'/'pc.jsonl';self.log.parent.mkdir(parents=True)
        (self.root/'runs'/'active.json').write_text(json.dumps(dict(logPath=str(self.log),runId='one')))
        self.log.touch();self.activity=Activity(self.root)

    def tearDown(self): self.temp.cleanup()

    def packet(self,kind='sensor_event',valid=True,age=0):
        return dict(kind='packet',pcMonotonicNs=time.monotonic_ns()-int(age*1e9),
            packet=dict(kind=kind,valid=valid,running=True,registered=True),sequenceStep=1)

    def append(self,row):
        with self.log.open('a') as out:out.write(json.dumps(row)+'\n')

    def test_heartbeat_is_not_a_valid_sensor_event(self):
        self.append(self.packet('heartbeat'));state=self.activity.update(prime=True)
        self.assertFalse(state['freshValid']);self.assertEqual(state['validEvents'],0)

    def test_prime_excludes_history_and_counts_new_events(self):
        self.append(self.packet());state=self.activity.update(prime=True)
        self.assertTrue(state['freshValid']);self.assertEqual(state['validEvents'],0)
        self.append(self.packet());self.append(self.packet('heartbeat'))
        state=self.activity.update()
        self.assertEqual(state['packets'],2);self.assertEqual(state['validEvents'],1)

    def test_old_sensor_and_invalid_heartbeat_do_not_pass(self):
        self.append(self.packet(age=40));self.append(self.packet('heartbeat'))
        self.assertFalse(self.activity.update(prime=True)['freshValid'])
        self.append(self.packet());self.append(self.packet('heartbeat',False))
        self.assertFalse(self.activity.update()['freshValid'])

    def test_disconnect_invalidates_recent_sensor(self):
        self.append(self.packet());self.append(dict(kind='wifi_disconnected'))
        self.assertFalse(self.activity.update(prime=True)['freshValid'])

    def test_partial_json_line_waits_for_completion(self):
        self.activity.update(prime=True)
        text=json.dumps(self.packet())
        with self.log.open('a') as out:out.write(text[:10])
        self.assertEqual(self.activity.update()['packets'],0)
        with self.log.open('a') as out:out.write(text[10:]+'\n')
        self.assertEqual(self.activity.update()['validEvents'],1)

    def test_reject_log_outside_runs(self):
        with self.assertRaises(ValueError): receiver_path(self.root,dict(logPath=str(self.root/'pc.jsonl')))


class IdentityTest(unittest.TestCase):
    def test_optional_bridge_and_registered_mcp_exclude_agent_and_reused_pids(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); (root/'runs/mcp-adapters').mkdir(parents=True)
            stamp='2026-09-27T05:10:14.1103568Z'; ticks=iso_filetime(stamp)
            executable=str(root/'python.exe')
            for file,pid in (('active.json',1),('dashboard.json',2)):
                (root/'runs'/file).write_text(json.dumps(dict(pid=pid,processStartUtc=stamp)))
            (root/'runs/capability-service.json').write_text(json.dumps(dict(pid=3,creationTicks='20',executable=executable)))
            record=root/'runs/mcp-adapters/4-30.json'
            record.write_text(json.dumps(dict(schema=1,component='mcp_adapter',project=str(root),pid=4,creationTicks='30',executable=executable)))
            class FakeWindows:
                reused=False
                unreadable=False
                def processes(self):return {pid:dict(pid=pid,parent=0,threads=1) for pid in range(1,6)}
                def process(self,pid,threads=None):
                    if pid==4 and self.unreadable: raise PermissionError(13,'access denied')
                    created={1:ticks,2:ticks,3:20,4:99 if self.reused else 30,5:40}[pid]
                    return dict(process(pid=pid,creation=created),executable=executable)
            windows=FakeWindows()
            rows,alive,issues=Targets(root,windows).sample()
            self.assertEqual({p['pid']:p['group'] for p in rows},{1:'receiver',2:'dashboard',3:'capability_bridge',4:'mcp_adapter'})
            self.assertTrue(all(alive.values())); self.assertEqual(issues,[])
            windows.reused=True
            rows,_,_=Targets(root,windows).sample()
            self.assertEqual({p['pid'] for p in rows},{1,2,3})
            windows.reused=False; windows.unreadable=True
            rows,_,issues=Targets(root,windows).sample()
            self.assertEqual({p['pid'] for p in rows},{1,2,3})
            self.assertEqual([r['group'] for r in issues],['mcp_adapter'])

    def test_dotnet_fraction_preserves_100ns(self):
        self.assertEqual(iso_filetime('2026-09-27T05:10:14.1103568Z')-iso_filetime('2026-09-27T05:10:14.1103560+00:00'),8)

    def test_reused_root_pid_never_attributes_unrelated_process(self):
        class FakeWindows:
            def processes(self):return {1:dict(pid=1,parent=0,threads=1)}
            def process(self,pid,threads=None):return dict(process(),executable='other.exe')
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/'runs').mkdir()
            for file in ('active.json','dashboard.json'):
                (root/'runs'/file).write_text(json.dumps(dict(pid=1,processStartUtc='2026-09-27T05:10:14.1103568Z')))
            rows,alive,issues=Targets(root,FakeWindows()).sample()
            self.assertEqual(rows,[]);self.assertFalse(any(alive.values()));self.assertEqual(len(issues),2)


if __name__=='__main__':unittest.main()
