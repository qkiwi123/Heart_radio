import tempfile
import threading
import unittest
from unittest.mock import Mock
from data_access import DataAccess


class HeartOnceTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.state=dict(nowMs=100000,connected=True,valid=False,bpm=None,eventAgeSeconds=None)
        self.inventory=dict(status='ok',model='OW20W2',sdk=27,firmware='OW20W2_11_A.92',sensors=[dict(sensorId=0,name='HR',type=21)])
        self.result=dict(status='data_received',sensor=dict(name='HR',type=21),samples=[dict(values=[73],accuracy=3,eventNs=1,watchReceiptWallMs=2)],callbacks=1,listenersReleased=True,requestWakeLockHeldAtResult=False,continuousHeartPreserved=False,heartSensorOwnersAfter=0)
        self.wire=Mock(side_effect=lambda op,params=None,request_id=None:self.inventory if op=='inventory' else dict(self.result,id=request_id))
        self.access=DataAccess(self.folder.name,lambda:self.state,self.wire)
    def test_default_fifteen_seconds_and_fresh_result_cached_without_broadcast(self):
        result=self.access.call('measure_heart_rate',{})
        self.assertTrue(result['ok']);self.assertEqual(result['value'],73);self.assertEqual(result['source'],'on_demand_heart_rate')
        self.assertEqual(self.wire.call_args.args,('heart_sample',dict(sensorId=0,seconds=15)))
        self.assertFalse(result['continuousHeartPreserved']);self.assertEqual(result['heartSensorOwnersAfter'],0)
        self.wire.reset_mock();cached=self.access.call('get_heart_rate',{})
        self.assertEqual(cached['value'],73);self.assertTrue(cached['cached']);self.wire.assert_not_called()
        saved=self.access.cached()['measurements']['heart_rate_once'];self.assertEqual(saved['value'],73);self.assertTrue(saved['cacheReusable'])
        settings=self.access.policy.read();settings['allowedMetrics'].remove('heart_rate');self.access.policy.save(settings)
        self.assertNotIn('heart_rate_once',self.access.cached()['measurements'])
    def test_timeout_has_no_number_no_retry_and_keeps_diagnostic(self):
        self.result.update(status='timeout',samples=[],callbacks=0)
        result=self.access.call('measure_heart_rate',{})
        self.assertFalse(result['ok']);self.assertIsNone(result['value']);self.assertEqual(result['status'],'timeout')
        self.assertEqual(self.wire.call_count,2)
    def test_limit_types_and_permission_apply_before_wire(self):
        for seconds in (16,True,0):
            with self.assertRaises(ValueError):self.access.call('measure_heart_rate',dict(seconds=seconds))
        settings=self.access.policy.read();settings['allowedMetrics'].remove('heart_rate');self.access.policy.save(settings)
        self.assertEqual(self.access.call('measure_heart_rate',{})['status'],'disabled_by_settings');self.wire.assert_not_called()
    def test_existing_broadcast_value_remains_local_and_takes_precedence(self):
        self.state.update(valid=True,bpm=81,eventAgeSeconds=1)
        value=self.access.call('get_heart_rate',{});self.assertEqual(value['value'],81);self.wire.assert_not_called()
    def test_unconfirmed_release_cannot_be_shown_as_success(self):
        self.result['listenersReleased']=False;value=self.access.call('measure_heart_rate',{})
        self.assertFalse(value['ok']);self.assertEqual(value['status'],'cleanup_unconfirmed');self.assertIsNone(value['value'])
    def test_targeted_cancel_and_busy_share_existing_measurement_slot(self):
        entered=threading.Event();released=threading.Event()
        def wire(op,params=None,request_id=None):
            if op=='inventory':return self.inventory
            if op=='heart_sample':entered.set();released.wait(3);return dict(self.result,status='cancelled',samples=[],id=request_id)
            if op=='cancel':released.set();return dict(status='ok')
        self.wire.side_effect=wire;values=[]
        thread=threading.Thread(target=lambda:values.append(self.access.call('measure_heart_rate',{})));thread.start()
        try:
            self.assertTrue(entered.wait(1));active=self.access.measurement_state()
            self.assertEqual(active['metric'],'heart_rate');self.assertEqual(active['seconds'],15)
            self.assertEqual(self.access.call('measure_pressure',{})['status'],'not_available')
            self.assertEqual(self.access.call('cancel_measurement',dict(request_id=active['requestId']))['status'],'cancel_requested')
        finally:released.set();thread.join(3)
        self.assertEqual(values[0]['status'],'cancelled');self.assertIsNone(self.access.measurement_state())


if __name__=='__main__':unittest.main()
