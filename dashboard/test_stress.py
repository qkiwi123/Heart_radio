import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from data_access import DataAccess
from health_protocol import health_log_summary, validate_health_request


class StressTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.permission = Mock(return_value=True)
        self.bridge = Mock()
        self.access = DataAccess(directory.name, lambda: {}, self.bridge, wear_permitted=self.permission)

    @staticmethod
    def reading(op, params, request_id):
        now = time.time_ns() // 1_000_000
        return dict(op=op, id=request_id, status='ok', score=49, source='stock_pressure_page', pageStage='measuring_seen',
                    measuringSeenAtMs=now-25_000, observedAtMs=now)

    def test_fresh_score_and_rest_uncertainty(self):
        self.bridge.side_effect = self.reading
        result = self.access.measure_stress('unknown')
        self.assertEqual((result['status'], result['score'], result['reportedWearerState']), ('ok', 49, 'unknown'))
        self.assertFalse(result['restVerified'])
        self.assertFalse(result['accuracyVerified'])
        self.assertIsNone(self.access.measurement_state())
        self.assertIsNone(self.access.recent('stress', 60))

    def test_known_motion_or_wear_privacy_prevents_request(self):
        self.assertEqual(self.access.measure_stress('moving')['status'], 'skipped_moving')
        self.permission.return_value = False
        self.assertEqual(self.access.measure_stress('unknown')['status'], 'disabled_by_settings')
        with self.assertRaises(ValueError):
            from user_state import selected_state_metrics
            selected_state_metrics({'metrics':['stress'], 'wearer_state':'walking'})
        self.bridge.assert_not_called()

    def test_no_score_on_failure_stale_result_or_changed_permission(self):
        for invalid in (dict(status='no_fresh_result'), dict(status='off_wrist'), dict(status='accessibility_disabled'),
                        dict(score=0), dict(score=101), dict(score=True), dict(id='previous'), dict(op='health_now'),
                        dict(source='last_pressure_tv'), dict(pageStage='stock_page_seen'),
                        dict(measuringSeenAtMs=0), dict(observedAtMs=1)):
            with self.subTest(invalid=invalid):
                self.bridge.side_effect = lambda *a, **kw: dict(self.reading(*a, **kw), **invalid)
                result = self.access.measure_stress('still')
                self.assertFalse(result['ok'])
                self.assertNotIn('score', result)
        self.permission.side_effect = [True, False]
        self.bridge.side_effect = self.reading
        self.assertNotIn('score', self.access.measure_stress('unknown'))

    def test_busy_and_cancel_do_not_accept_late_result(self):
        # Cancelled callers must not be shown a late stock score.
        started, ended = threading.Event(), threading.Event()
        def request(op, params, request_id=None):
            if op == 'cancel':
                self.assertEqual(self.access.measurement_state()['requestId'], params['targetId'])
                ended.set()
                return {'status':'ok'}
            started.set()
            self.assertTrue(ended.wait(4))
            return self.reading(op, params, request_id)
        self.bridge.side_effect = request
        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = worker.submit(self.access.measure_stress, 'unknown')
            self.assertTrue(started.wait(2))
            active = self.access.capabilities(compact=True)['measurement']['requests']
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0]['metric'], 'stress')
            self.assertEqual(active[0]['requestId'], self.access.measurement_state()['requestId'])
            self.assertNotIn('seconds', active[0])
            self.assertEqual(self.access.measure_stress('unknown')['status'], 'busy')
            self.assertTrue(self.access.call('cancel_measurement', {})['ok'])
            result = pending.result()
        self.assertEqual(result['status'], 'cancelled')
        self.assertNotIn('score', result)

    def test_timeout_and_confirmed_stock_failure_have_distinct_explanations(self):
        for status, words in [('no_fresh_result', '65 秒'), ('stock_no_valid_reading', '未测得有效值')]:
            with self.subTest(status=status):
                self.bridge.side_effect = lambda *a, **kw: dict(self.reading(*a, **kw), status=status)
                result = self.access.measure_stress('unknown')
                self.assertFalse(result['ok'])
                self.assertEqual(result['status'], status)
                self.assertIn(words, result['message'])
                self.assertNotIn('score', result)
        for stale in (dict(id='other'), dict(pageStage='stock_page_seen')):
            self.bridge.side_effect = lambda *a, **kw: dict(self.reading(*a, **kw), status='stock_no_valid_reading', **stale)
            self.assertEqual(self.access.measure_stress('unknown')['status'], 'invalid_result')

    def test_wire_fields_and_diagnostic_redaction(self):
        validate_health_request('health_stress', {})
        with self.assertRaises(ValueError):
            validate_health_request('health_stress', {'score': 49})
        self.assertNotIn('score', health_log_summary(self.reading('health_stress', {}, 'test')))
