import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from data_access import DataAccess


class ConcurrentReadingTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.types={'heart_rate':21,'pressure':6,'accelerometer':1,'gyroscope':4}
        self.sensors=[dict(sensorId=i,name=metric,type=typ) for i,(metric,typ) in enumerate(self.types.items())]
        self.started={metric:threading.Event() for metric in self.types}
        self.release={metric:threading.Event() for metric in self.types}
        self.calls=[];self.call_lock=threading.Lock()
        self.access=DataAccess(self.folder.name,lambda:dict(valid=False,connected=True,nowMs=1,eventAgeSeconds=None),self.bridge)

    def bridge(self,op,params=None,request_id=None):
        if op=='inventory':return dict(status='ok',model='OW20W2',sdk=27,firmware='OW20W2_11_A.92',sensors=self.sensors)
        if op=='status':return dict(status='ok',batteryPct=80,plugged=0)
        if op=='cancel':
            active=self.access.measurement_state()
            row=next(row for row in active['requests'] if row['requestId']==params['targetId'])
            self.release[row['metric']].set();return dict(status='ok')
        sensor=self.sensors[params['sensorId']];metric=sensor['name']
        with self.call_lock:self.calls.append(metric)
        self.started[metric].set()
        if not self.release[metric].wait(4):raise TimeoutError('test release missing')
        active=self.access.measurement_state()
        cancelled=next(row for row in active['requests'] if row['metric']==metric)['cancelRequested']
        return dict(status='cancelled' if cancelled else 'data_received',id=request_id,sensor=sensor,
                    samples=[dict(values=[75] if metric=='heart_rate' else [1000] if metric=='pressure' else [1,2,3])],
                    callbacks=1,listenersReleased=True,requestWakeLockHeldAtResult=False)

    def release_all(self):
        for event in self.release.values():event.set()

    def test_all_four_overlap_and_cancelling_one_preserves_the_other_three(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            futures={metric:workers.submit(self.access.measure,metric,3,self.access.policy.read(),True) for metric in self.types}
            try:
                for event in self.started.values():self.assertTrue(event.wait(2))
                state=self.access.measurement_state();self.assertEqual(state['count'],4)
                gyro=next(row for row in state['requests'] if row['metric']=='gyroscope')
                self.assertEqual(self.access.cancel(gyro['requestId'])['status'],'cancel_requested')
                self.assertEqual(futures['gyroscope'].result(2)['status'],'cancelled')
                self.assertEqual({row['metric'] for row in self.access.measurement_state()['requests']},set(self.types)-{'gyroscope'})
                self.release_all()
                for metric in ('heart_rate','pressure','accelerometer'):self.assertTrue(futures[metric].result(2)['ok'])
            finally:self.release_all()
        self.assertIsNone(self.access.measurement_state())

    def test_identical_in_flight_calls_share_one_sensor_request(self):
        with ThreadPoolExecutor(max_workers=2) as workers:
            first=workers.submit(self.access.call,'measure_pressure',dict(seconds=3,fresh=True))
            self.assertTrue(self.started['pressure'].wait(2))
            second=workers.submit(self.access.call,'measure_pressure',dict(seconds=3,fresh=True))
            try:
                time.sleep(.05)
                self.assertFalse(second.done());self.assertEqual(self.calls,['pressure'])
                self.release['pressure'].set()
                a,b=first.result(2),second.result(2)
                self.assertEqual(a['requestId'],b['requestId']);self.assertTrue(b['sharedInFlight'])
            finally:self.release_all()

    def test_cooldown_is_per_metric_and_can_be_zero(self):
        self.release_all();settings=dict(self.access.policy.read(),measurementCooldownSeconds=15)
        self.assertTrue(self.access.measure('pressure',1,settings)['ok'])
        self.assertEqual(self.access.measure('pressure',1,settings)['status'],'rate_limited')
        self.assertTrue(self.access.measure('gyroscope',1,settings)['ok'])
        replacement=DataAccess(self.folder.name,self.access.heart_snapshot,self.bridge)
        self.assertEqual(replacement.measure('pressure',1,settings)['status'],'rate_limited')
        self.assertTrue(self.access.measure('pressure',1,dict(settings,measurementCooldownSeconds=0))['ok'])

    def test_batch_selected_combination_is_parallel_and_partial_failure_is_explicit(self):
        with ThreadPoolExecutor(max_workers=1) as caller:
            future=caller.submit(self.access.call,'read_watch_data',dict(metrics=['pressure','accelerometer','gyroscope'],seconds=2,fresh=True))
            try:
                for metric in ('pressure','accelerometer','gyroscope'):self.assertTrue(self.started[metric].wait(2))
                self.assertFalse(self.started['heart_rate'].is_set());self.release_all()
                result=future.result(2);self.assertTrue(result['complete']);self.assertEqual(set(result['results']),{'pressure','accelerometer','gyroscope'})
            finally:self.release_all()
        policy=self.access.policy.read();policy['allowedMetrics'].remove('pressure');self.access.policy.save(policy)
        result=self.access.call('read_watch_data',dict(metrics=['pressure','device_status']))
        self.assertTrue(result['ok']);self.assertFalse(result['complete']);self.assertEqual(result['status'],'partial')
        self.assertEqual(result['failedMetrics'],['pressure']);json.dumps(result,allow_nan=False)

    def test_invalid_combinations_do_not_start_measurement(self):
        for metrics in ([],['pressure','pressure'],['location'],[1],True):
            with self.assertRaises(ValueError):self.access.call('read_watch_data',dict(metrics=metrics))
        self.assertEqual(self.calls,[])

    def test_combined_read_defaults_to_five_second_heart_wait_and_keeps_single_tool_limit(self):
        self.release_all()
        combined=self.access.call('read_watch_data',dict(metrics=['heart_rate'],fresh=True))
        self.assertEqual(combined['results']['heart_rate']['requestedSeconds'],5)
        single=self.access.call('measure_heart_rate',{})
        self.assertEqual(single['requestedSeconds'],15)


if __name__=='__main__':unittest.main()
