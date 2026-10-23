import json
from pathlib import Path
import tempfile
import unittest
from data_access import DataAccess


class DataAccessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.calls = []
        self.state = dict(nowMs=100000, connected=True, valid=True, bpm=75, eventAgeSeconds=3,
                          measurementMs=90000, accuracy=3, status='正在接收心率')
        self.result = dict(status='data_received', id='r1', listenersReleased=True, requestWakeLockHeldAtResult=False,
                           sensor=dict(name='pressure', type=6), callbacks=2,
                           samples=[dict(values=[1000], accuracy=3), dict(values=[1002], accuracy=3)])
        self.connection = dict(status='watch_offline', connected=False)
        self.access = DataAccess(self.directory.name, lambda: self.state, self.bridge, lambda: self.connection)

    def bridge(self, op, params=None, request_id=None):
        self.calls.append((op, params))
        if op == 'inventory':
            return dict(status='ok', model='OW20W2', sdk=27, firmware='OW20W2_11_A.92',
                        sensors=[dict(name='pressure', type=6, sensorId=21)])
        return self.result

    def test_hr_cache_only_and_stricter_age(self):
        self.assertEqual(self.access.call('get_heart_rate', {})['value'], 75)
        result = self.access.call('get_heart_rate', dict(max_age_seconds=2))
        self.assertFalse(result['ok'])
        self.assertIsNone(result['value'])
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(self.calls, [])

    def test_invalid_live_state_does_not_return_old_value(self):
        self.state['valid'] = False
        self.assertIsNone(self.access.call('get_heart_rate', {})['value'])
        self.state['connected'] = False
        self.assertEqual(self.access.call('get_heart_rate', {})['status'], 'disconnected')

    def test_index_from_current_inventory_and_independent_metric(self):
        result = self.access.call('measure_pressure', dict(seconds=4))
        self.assertTrue(result['ok'])
        self.assertEqual(result['value'], 1001)
        self.assertEqual(self.calls[-1], ('sample', dict(sensorId=21, seconds=4)))
        self.assertEqual(self.access.call('measure_motion', {})['status'], 'not_available')
        cache = DataAccess(self.directory.name, lambda: self.state, self.bridge, lambda: self.connection).cached()
        self.assertEqual(cache['measurements']['pressure']['value'], 1001)

    def test_mismatched_sensor_and_unreleased_lock_hide_values(self):
        self.result['sensor']['type'] = 65536
        self.assertEqual(self.access.call('measure_pressure', {})['status'], 'sensor_mismatch')
        self.access.last_measure_end.clear()
        self.result['sensor']['type'] = 6
        self.result['requestWakeLockHeldAtResult'] = True
        result = self.access.call('measure_pressure', {})
        self.assertFalse(result['ok'])
        self.assertIsNone(result['value'])
        self.assertEqual(result['samples'], [])

    def test_reject_boolean_duration_and_unknown_params_without_bridge(self):
        for name, args in [('measure_pressure', dict(seconds=True)), ('measure_motion', dict(kind='vendor_ppg')),
                           ('get_heart_rate', dict(max_age_seconds=31)), ('get_device_status', dict(force=True))]:
            with self.assertRaises(ValueError): self.access.call(name, args)
        self.assertEqual(self.calls, [])

    def test_nonfinite_samples_not_serialized_as_measurements(self):
        self.result['samples'] = [dict(values=[float('nan')]), dict(values=[None])]
        result = self.access.call('measure_pressure', {})
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'no_valid_samples')
        json.dumps(result, allow_nan=False)

    def test_shared_device_status_cache_avoids_repeated_watch_queries(self):
        self.result = dict(status='ok', batteryPct=65, plugged=0, batteryTemperatureTenthsC=245)
        first = self.access.call('get_device_status', {})
        second = self.access.call('get_device_status', {})
        self.assertEqual(first['data']['batteryTemperatureC'], 24.5)
        self.assertTrue(second['cached'])
        self.assertEqual(self.calls, [('status', None)])

    def test_heart_receipt_timestamp_is_preserved_when_wall_clock_changes(self):
        self.state.update(eventReceivedMs=97000, nowMs=200000)
        self.assertEqual(self.access.call('get_heart_rate', {})['receivedAtMs'], 97000)

    def test_connection_refresh_never_requests_watch_measurement(self):
        self.assertFalse(self.access.cached()['bridge']['connected'])
        self.connection.update(connected=True, connectionSerial=2)
        self.assertEqual(self.access.cached()['bridge']['connectionSerial'], 2)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
