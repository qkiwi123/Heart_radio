import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from data_access import DataAccess
from data_policy import DataPolicy, DEFAULTS


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.policy = DataPolicy(self.temp.name)

    def test_saved_settings_survive_a_new_service_and_invalid_updates_preserve_file(self):
        value = dict(DEFAULTS, measurementSeconds=7, allowedMetrics=['pressure'])
        self.policy.save(value)
        self.assertEqual(DataPolicy(self.temp.name).read(), value)
        before = self.policy.path.read_bytes()
        for change in (dict(measurementSeconds=True), dict(measurementCooldownSeconds=14),
                       dict(sensorCacheSeconds=301), dict(pcServiceSeconds=61),
                       dict(allowedMetrics=['pressure','pressure']),dict(allowedMetrics=['location'])):
            with self.assertRaises(ValueError): self.policy.save(dict(value, **change))
            self.assertEqual(self.policy.path.read_bytes(), before)

    def test_corrupt_settings_are_reported_instead_of_silently_enabling_metrics(self):
        self.policy.path.parent.mkdir(parents=True)
        self.policy.path.write_text('{}')
        with self.assertRaises(OSError): self.policy.read()


class SharedSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.calls = []
        self.state = dict(nowMs=1000,valid=True,connected=True,bpm=70,eventAgeSeconds=5)
        self.access = DataAccess(self.temp.name,lambda:self.state,self.bridge,lambda:dict(connected=True))
        self.inventory = dict(status='ok',model='OW20W2',sdk=27,firmware='OW20W2_11_A.92',sensors=[dict(type=6,name='pressure',sensorId=3)])
        self.raw = dict(status='data_received',sensor=dict(type=6,name='pressure'),samples=[dict(values=[1010])],
                        listenersReleased=True,requestWakeLockHeldAtResult=False)

    def bridge(self,op,params=None,request_id=None):
        self.calls.append((op,params,request_id))
        return self.inventory if op=='inventory' else dict(self.raw,id=request_id)

    def settings(self,**changes):
        self.access.policy.save(dict(self.access.policy.read(),**changes))

    def test_disabled_metrics_cannot_be_reenabled_by_fresh_or_direct_tool_arguments(self):
        self.settings(allowedMetrics=[])
        for name,args in [('get_heart_rate',{}),('get_device_status',dict(fresh=True)),
                          ('measure_pressure',dict(seconds=10,fresh=True)),('measure_motion',{})]:
            self.assertEqual(self.access.call(name,args)['status'],'disabled_by_settings')
        self.assertEqual(self.calls,[])

    def test_saved_duration_and_stricter_heart_age_apply_to_all_calls(self):
        self.settings(measurementSeconds=7,heartMaxAgeSeconds=3)
        self.assertEqual(self.access.call('get_heart_rate',dict(max_age_seconds=30))['status'],'stale')
        result=self.access.call('measure_pressure',{})
        self.assertEqual(result['requestedSeconds'],7)
        self.assertEqual(self.calls[-1][1]['seconds'],7)

    def test_reusing_sensor_cache_does_not_measure_and_fresh_still_obeys_cooldown(self):
        self.settings(sensorCacheSeconds=60)
        first=self.access.call('measure_pressure',{})
        second=self.access.call('measure_pressure',{})
        self.assertEqual(first['requestId'],second['requestId'])
        self.assertTrue(second['cached'])
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.access.call('measure_pressure',dict(fresh=True))['status'],'rate_limited')
        self.assertEqual(len(self.calls),2)
        replacement=DataAccess(self.temp.name,lambda:self.state,self.bridge,lambda:{})
        self.assertEqual(replacement.call('measure_pressure',dict(fresh=True))['status'],'rate_limited')

    def test_failed_cached_result_is_never_returned_as_a_reusable_success(self):
        self.settings(sensorCacheSeconds=60)
        self.raw['status']='request_timeout'
        self.access.call('measure_pressure',{})
        self.assertEqual(self.access.call('measure_pressure',{})['status'],'rate_limited')
        self.assertIsNone(self.access.measurement_state())

    def test_targeted_cancel_and_simultaneous_clients_release_only_the_current_request(self):
        sampled=threading.Event(); cancelled=threading.Event(); result=[]
        def bridge(op,params=None,request_id=None):
            if op=='inventory':return self.inventory
            if op=='sample':
                sampled.set();self.assertTrue(cancelled.wait(3))
                return dict(self.raw,id=request_id,status='cancelled')
            self.assertEqual(params['targetId'],self.access.measurement_state()['requestId'])
            cancelled.set();return dict(status='ok')
        self.access.bridge=bridge
        worker=threading.Thread(target=lambda:result.append(self.access.call('measure_pressure',{})))
        worker.start();self.addCleanup(lambda:worker.join(3))
        self.assertTrue(sampled.wait(3))
        self.assertEqual(self.access.call('measure_pressure',{})['status'],'busy')
        self.assertEqual(self.access.call('cancel_measurement',dict(request_id='a'*32))['status'],'no_active_measurement')
        active=self.access.measurement_state()
        self.assertEqual(self.access.call('cancel_measurement',dict(request_id=active['requestId']))['status'],'cancel_requested')
        worker.join(3);self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]['status'],'cancelled')
        self.assertEqual(result[0]['samples'],[])
        self.assertIsNone(result[0]['value'])
        self.assertTrue(result[0]['listenersReleased'])
        self.assertIsNone(self.access.measurement_state())
        self.assertTrue(self.access.measure_lock.acquire(blocking=False));self.access.measure_lock.release()

    def test_cancelling_during_inventory_never_starts_a_sensor(self):
        preparing=threading.Event();release=threading.Event();result=[]
        def bridge(op,params=None,request_id=None):
            self.assertEqual(op,'inventory');preparing.set();self.assertTrue(release.wait(3));return self.inventory
        self.access.bridge=bridge
        worker=threading.Thread(target=lambda:result.append(self.access.call('measure_pressure',{})))
        worker.start();self.assertTrue(preparing.wait(3))
        self.assertEqual(self.access.call('cancel_measurement',{})['status'],'cancel_requested')
        release.set();worker.join(3);self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]['status'],'cancelled')

    def test_transport_exception_releases_local_measurement_lock(self):
        self.access.bridge=lambda *args,**kwargs:(_ for _ in ()).throw(OSError('closed'))
        with self.assertRaises(OSError):self.access.call('measure_pressure',{})
        self.assertIsNone(self.access.measurement_state())
        self.assertTrue(self.access.measure_lock.acquire(blocking=False));self.access.measure_lock.release()


if __name__=='__main__':unittest.main()
