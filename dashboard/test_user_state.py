import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from data_access import DataAccess
from user_state import classify


def window(typ, values=None):
    values=values or [.03 if typ==1 else .02]*30
    return dict(ok=True,status='data_received',sensor=dict(type=typ,name='a' if typ==1 else 'g'),summaryVersion=1,
        requestedSeconds=30,truncated=False,listenersReleased=True,requestWakeLockHeldAtResult=False,
        sampleCount=30*26,callbacks=30*26,invalidEvents=0,actualElapsedMs=30020,arrivalSpanMs=29800,
        maximumArrivalGapMs=90,watchBeginElapsedNs=1000000,rms=sum(values)/len(values),
        bins=[dict(second=i,count=26,rms=value) for i,value in enumerate(values)])


def hearts():
    return [dict(ok=True,source='broadcast',receivedAtMs=t,value=68+t%3) for t in range(1000,30000,2000)]


class RuleTests(unittest.TestCase):
    def run_rule(self,a=None,g=None,h=None):
        return classify(a or window(1),g or window(4),hearts() if h is None else h,0,30000)
    def test_three_tiers_and_motion_is_not_overridden_by_stable_heart(self):
        self.assertEqual(self.run_rule()['tier'],1)
        self.assertEqual(self.run_rule(window(1,[.25]*30),window(4,[.15]*30))['tier'],2)
        self.assertEqual(self.run_rule(window(1,[2]*30),window(4,[2]*30))['tier'],3)
    def test_exact_half_and_less_than_half_have_different_tiers(self):
        for n,tier in [(14,2),(15,3)]:
            self.assertEqual(self.run_rule(window(1,[1]*n+[.2]*(30-n)),window(4,[.2]*30))['tier'],tier)
    def test_low_motion_without_heart_is_unknown_and_repeated_cache_is_not_many_events(self):
        self.assertIsNone(self.run_rule(h=[])['tier'])
        result=self.run_rule(h=[hearts()[-1]]*100)
        self.assertIsNone(result['tier']);self.assertEqual(result['heartRate']['validReceipts'],1)
        old=[dict(x,receivedAtMs=x['receivedAtMs']-60000) for x in hearts()]
        self.assertIsNone(self.run_rule(h=old)['tier'])
    def test_unstable_heart_does_not_force_low_motion_into_tier_two(self):
        values=[dict(x,value=60+i*3) for i,x in enumerate(hearts())]
        self.assertIsNone(self.run_rule(h=values)['tier'])
    def test_incomplete_or_misaligned_windows_never_classify(self):
        for patch in [dict(truncated=True),dict(listenersReleased=False),dict(requestWakeLockHeldAtResult=True),
                      dict(actualElapsedMs=10000),dict(arrivalSpanMs=2500),dict(maximumArrivalGapMs=2000),
                      dict(rms=float('nan')),dict(watchBeginElapsedNs=600000000)]:
            with self.subTest(patch=patch):self.assertIsNone(self.run_rule(a=dict(window(1),**patch))['tier'])
        a=window(1);a['bins'][1]['second']=0
        self.assertIsNone(self.run_rule(a=a)['tier'])
        a=window(1);a['bins']=a['bins'][:12];a['sampleCount']=a['callbacks']=12*26
        self.assertIsNone(self.run_rule(a=a)['tier'])
        for patch in [dict(bins=None),dict(bins=[1]),dict(sensor=[]),dict(invalidEvents=-1),dict(callbacks=True)]:
            with self.subTest(patch=patch):self.assertIsNone(self.run_rule(a=dict(window(1),**patch))['tier'])
    def test_summary_is_small_and_contains_no_raw_arrays_or_confirmed_wakefulness(self):
        value=self.run_rule();encoded=json.dumps(value,ensure_ascii=False,allow_nan=False)
        self.assertLess(len(encoded),1500)
        self.assertNotIn('bins',encoded);self.assertNotIn('samples',encoded);self.assertNotIn('awake',value)
        self.assertEqual(value['wearBasis'],'service_available_assumption')


class StateToolTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup);self.calls=[]
        self.sensors=[dict(type=1,name='a',sensorId=1),dict(type=4,name='g',sensorId=2)]
        self.access=DataAccess(self.folder.name,lambda:dict(connected=False,valid=False,nowMs=1,eventAgeSeconds=None),self.bridge)
    def bridge(self,op,params=None,request_id=None):
        self.calls.append((op,params))
        if op=='inventory':return dict(status='ok',model='OW20W2',sdk=27,firmware='OW20W2_11_A.92',motionWindowVersion=1,sensors=self.sensors)
        if op=='motion_window':
            typ=self.sensors[params['sensorId']-1]['type'];return dict(window(typ,[2]*30),id=request_id)
        raise AssertionError(op)
    def test_fresh_bounded_observation_and_no_new_history_or_old_cache_replacement(self):
        value=self.access.call('get_user_state',{})
        self.assertEqual(value['tier'],3)
        self.assertEqual(len([x for x in self.calls if x[0]=='motion_window']),2)
        self.assertTrue(all(x[1]['seconds']==30 for x in self.calls if x[0]=='motion_window'))
        self.assertFalse((self.access.directory/'latest.json').exists())
        self.assertFalse((self.access.directory/'tools.jsonl').exists())
        self.assertIsNone(self.access.measurement_state())
    def test_required_metric_permission_is_checked_before_any_watch_call(self):
        settings=self.access.policy.read();settings['allowedMetrics'].remove('gyroscope');self.access.policy.save(settings)
        self.assertEqual(self.access.call('get_user_state',{})['status'],'disabled_by_settings');self.assertEqual(self.calls,[])
    def test_heart_permission_is_respected_and_optional_for_tier_three(self):
        settings=self.access.policy.read();settings['allowedMetrics'].remove('heart_rate');self.access.policy.save(settings)
        self.access.heart_snapshot=lambda:(_ for _ in ()).throw(AssertionError('heart must not be read'))
        self.assertEqual(self.access.call('get_user_state',{})['tier'],3)
    def test_old_watch_is_explicit_and_unknown_arguments_never_measure(self):
        original=self.bridge
        def old(op,params=None,request_id=None):
            value=original(op,params,request_id);value.pop('motionWindowVersion',None);return value
        self.access.bridge=old
        value=self.access.call('get_user_state',{});self.assertIsNone(value['tier']);self.assertEqual(value['reason'],'watch_update_required')
        self.assertFalse(any(x[0]=='motion_window' for x in self.calls))
        with self.assertRaises(ValueError):self.access.call('get_user_state',dict(seconds=30))
    def test_parallel_state_calls_are_not_duplicate_measurements(self):
        self.access.state_lock.acquire()
        try:self.assertEqual(self.access.call('get_user_state',{})['status'],'busy');self.assertEqual(self.calls,[])
        finally:self.access.state_lock.release()
    def test_cancellation_releases_both_windows_and_does_not_produce_a_tier(self):
        entered={1:threading.Event(),2:threading.Event()};released={1:threading.Event(),2:threading.Event()}
        def bridge(op,params=None,request_id=None):
            if op=='inventory':return self.bridge(op,params,request_id)
            if op=='cancel':
                active=self.access.measurement_state()['requests']
                row=next(r for r in active if r['requestId']==params['targetId'])
                released[1 if row['metric']=='accelerometer' else 2].set();return dict(status='ok')
            i=params['sensorId'];entered[i].set();self.assertTrue(released[i].wait(3))
            return dict(status='cancelled',listenersReleased=True,requestWakeLockHeldAtResult=False)
        self.access.bridge=bridge
        with ThreadPoolExecutor(max_workers=1) as worker:
            result=worker.submit(self.access.call,'get_user_state',{})
            try:
                self.assertTrue(entered[1].wait(2));self.assertTrue(entered[2].wait(2))
                self.access.cancel();self.assertIsNone(result.result(3)['tier'])
            finally:
                for e in released.values():e.set()
        self.assertIsNone(self.access.measurement_state())


if __name__=='__main__':unittest.main()
