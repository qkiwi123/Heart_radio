import http.client
from http.server import ThreadingHTTPServer
import json
from itertools import combinations
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import server
from data_access import DataAccess
from datetime import datetime
from health_history import BEIJING, HealthHistory, day_start
from reminders import Reminders


class ToolsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        state = dict(nowMs=100000, connected=True, valid=True, bpm=71, eventAgeSeconds=1)
        access = DataAccess(self.temp.name, lambda: state, lambda *args: dict(status='watch_offline'))
        override = patch.object(server, 'data_access', access)
        override.start()
        self.addCleanup(override.stop)
        self.reminder_wire=Mock(side_effect=lambda op,p:dict(status='issued',reminderId=p['reminderId']))
        self.reminders=Reminders(self.temp.name,self.reminder_wire)
        override_reminders=patch.object(server,'reminders',self.reminders)
        override_reminders.start();self.addCleanup(override_reminders.stop)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.worker.join()

    def request(self, method, path='/api/tools', value=None, extra=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port)
        headers = {'Content-Type': 'application/json', 'X-HeartRadio-Action': 'tools'}
        headers.update(extra or {})
        connection.request(method, path, body=json.dumps(value) if value is not None else None, headers=headers)
        response = connection.getresponse()
        status, body = response.status, response.read()
        connection.close()
        return status, body

    def test_hr_tool_and_cache_get_work_without_watch_bridge(self):
        status, body = self.request('POST', value=dict(name='get_heart_rate', arguments={}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['value'], 71)
        self.assertEqual(self.request('GET', '/api/data')[0], 200)

    def test_cross_origin_and_invalid_tool_args_rejected(self):
        for extra in ({'Origin': 'https://example.com'}, {'Host': 'example.com'}, {'X-HeartRadio-Action': ''}):
            self.assertEqual(self.request('POST', value=dict(name='measure_pressure', arguments={}), extra=extra)[0], 403)
        self.assertEqual(self.request('POST', value=dict(name='measure_pressure', arguments=dict(seconds=60)))[0], 400)
        self.assertEqual(self.request('POST', value=dict(name='vendor_ppg', arguments={}))[0], 400)

    def test_mcp_tool_entry_accepts_long_unicode_and_shares_web_record(self):
        args=dict(reminder_id='mcp',title='标题',body='字'*500,vibration=dict(count=3,pulse_ms=300,gap_ms=500))
        status,body=self.request('POST',value=dict(name='send_reminder',arguments=args))
        self.assertEqual(status,200);self.assertTrue(json.loads(body)['ok'])
        status,body=self.request('POST',value=dict(name='get_reminder_status',arguments=dict(reminder_id='mcp')))
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['receipt']['reminderId'],'mcp')
        self.reminder_wire.assert_called_once()

    def test_tool_entry_cannot_enable_interaction_or_bypass_nested_validation(self):
        for args in [dict(reminder_id='bad',title='x',body='y',enable=True),
                     dict(reminder_id='bad',title='x',body='y',vibration=dict(count=True))]:
            self.assertEqual(self.request('POST',value=dict(name='send_reminder',arguments=args))[0],400)
        self.reminder_wire.assert_not_called()

    def test_health_history_is_local_and_sleep_is_a_separate_observation(self):
        store = HealthHistory(self.temp.name)
        store.save_settings(dict(archiveEnabled=False, historyMetrics=['activity'], liveMetrics=['wear','current_sleep']))
        sleep = {'value':'unknown','rawCode':0}
        wear = {'metric':'wear','value':'worn','status':'ok'}
        native = Mock(side_effect=[{'results': {'wear': wear}}, {'results': {'current_sleep': sleep}}])
        state = Mock(return_value={'tier':2})
        with (
            patch.object(server, 'health_history', store),
            patch.object(server.health_sync, 'store', store),
            patch.object(server.health_sync, 'now', native),
            patch.object(server.data_access, 'user_state', state),
        ):
            status, body = self.request('POST',value=dict(name='get_watch_health_history',arguments={'metrics':['activity']}))
            self.assertEqual(status,200)
            self.assertEqual(json.loads(body)['source'],'pc_archive')
            native.assert_not_called()
            status, body = self.request('POST',value=dict(name='get_user_state',arguments={'include_sleep':True}))
            self.assertEqual(status,200)
            self.assertEqual(json.loads(body), {'tier':2, 'sleep':sleep, 'wear':wear, 'wearBasis':'watch_native',
                'requestedMetrics':['sleep','heart_rate','accelerometer','gyroscope']})
            self.assertEqual([entry.args[0] for entry in native.call_args_list], [{'metrics':['wear']}, {'metrics':['current_sleep']}])

    def test_user_state_redacts_wear_revoked_during_motion(self):
        store = HealthHistory(self.temp.name)
        store.save_settings(dict(archiveEnabled=False, historyMetrics=[], liveMetrics=['wear']))
        worn = {'value':'worn','status':'ok','rawCode':1}

        def revoke(_settings, _metrics, *, wear_confirmed):
            self.assertTrue(wear_confirmed)
            store.save_settings(dict(store.settings(), liveMetrics=[]))
            return dict(ok=True, status='classified', tier=2, label='活动较少', motion={'validSeconds':30},
                        summary='运动判断', wearBasis='service_available_assumption')

        with (patch.object(server.health_sync, 'store', store),
              patch.object(server.health_sync, 'now', return_value={'results': {'wear':worn}}),
              patch.object(server.data_access, 'user_state', side_effect=revoke)):
            status, body = self.request('POST', value=dict(name='get_user_state',arguments={'metrics':['accelerometer','gyroscope']}))
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result['wear'], dict(metric='wear', value='unknown', status='disabled_by_settings'))
        self.assertIsNone(result['tier'])
        self.assertEqual(result['reason'], 'wear_unconfirmed')
        self.assertNotIn('motion', result)
        self.assertNotIn('wearBasis', result)

    def test_unconfirmed_wear_skips_new_sampling_and_keeps_available_observations(self):
        wear = dict(value='unknown', status='request_timeout')
        sleep = dict(value='sleeping', status='ok', messageId=490, sourceAtMs=1794387548000)
        with (patch.object(server.health_sync, 'now', side_effect=[
                  {'results': {'wear':wear}}, {'results': {'current_sleep':sleep}}]),
              patch.object(server.health_sync, 'permitted_live_results', side_effect=lambda values: values),
              patch.object(server.data_access, 'bridge') as bridge):
            status, body = self.request('POST', value=dict(name='get_user_state',
                arguments={'metrics':['sleep','heart_rate','accelerometer','gyroscope']}))
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result['wear'], wear)
        self.assertEqual(result['sleep'], sleep)
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['tier'])
        self.assertEqual(result['results']['heart_rate']['value'], 71)
        self.assertEqual(result['results']['accelerometer']['status'], 'wear_unconfirmed')
        self.assertEqual(result['results']['gyroscope']['status'], 'wear_unconfirmed')
        bridge.assert_not_called()

    def test_user_state_without_metrics_only_queries_wear_even_when_worn(self):
        worn = dict(value='worn', status='ok')
        with (patch.object(server.health_sync, 'now', return_value={'results': {'wear':worn}}) as native,
              patch.object(server.health_sync, 'permitted_live_results', side_effect=lambda values: values),
              patch.object(server.data_access, 'user_state') as sensors):
            status, body = self.request('POST', value=dict(name='get_user_state', arguments={}))
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result['wear'], worn)
        self.assertEqual(result['requestedMetrics'], [])
        self.assertIsNone(result['tier'])
        native.assert_called_once_with({'metrics':['wear']})
        sensors.assert_not_called()

    def test_user_state_skips_motion_and_sleep_after_confirmed_off_wrist(self):
        wear = {'value':'off_wrist','status':'ok','rawCode':0,'fieldPresent':False}
        with patch.object(server.health_sync, 'now', return_value={'results': {'wear':wear}}) as native, \
                patch.object(server.data_access, 'user_state') as motion:
            status, body = self.request('POST',value=dict(name='get_user_state',arguments={'include_sleep':True}))
            self.assertEqual(status,200)
            result = json.loads(body)
            self.assertEqual((result['status'],result['tier'],result['wearBasis']), ('off_wrist',None,'watch_native'))
            self.assertEqual(result['wear'],wear)
            native.assert_called_once_with({'metrics':['wear']})
            motion.assert_not_called()

    def test_user_state_checks_wear_first_and_supports_all_metric_combinations(self):
        optional = ('sleep', 'heart_rate', 'accelerometer', 'gyroscope')
        worn = dict(value='worn', status='ok')
        sleep = dict(value='sleeping', status='ok', messageId=490, sourceAtMs=1770000000000)
        for count in range(len(optional) + 1):
            for subset in combinations(optional, count):
                with self.subTest(subset=subset):
                    calls = []

                    def observe(args):
                        metric = args['metrics'][0]
                        calls.append(metric)
                        return {'results': {metric: worn if metric == 'wear' else sleep}}

                    with (patch.object(server.health_sync, 'now', side_effect=observe),
                          patch.object(server.health_sync, 'permitted_live_results', side_effect=lambda items: items),
                          patch.object(server.data_access, 'user_state', return_value={'tier':None}) as sensors):
                        result = server.call_tool('get_user_state', {'metrics':list(subset)})
                    self.assertEqual(calls, ['wear', 'current_sleep'] if 'sleep' in subset else ['wear'])
                    self.assertEqual('sleep' in result, 'sleep' in subset)
                    self.assertEqual(result['requestedMetrics'], list(subset))
                    selected_sensors = tuple(metric for metric in subset if metric != 'sleep')
                    if selected_sensors:
                        self.assertEqual(sensors.call_args.args[1], selected_sensors)
                        self.assertTrue(sensors.call_args.kwargs['wear_confirmed'])
                    else:
                        sensors.assert_not_called()

    def test_mcp_reports_sleep_and_exit_from_valid_490_replies(self):
        store = HealthHistory(self.temp.name)
        store.save_settings(dict(archiveEnabled=False, historyMetrics=[], liveMetrics=['wear','current_sleep']))
        adapter = Path(__file__).resolve().parents[1] / 'agent' / 'mcp_server.py'
        frames = [dict(jsonrpc='2.0', id=1, method='initialize', params=dict(protocolVersion='2025-06-18')),
                  dict(jsonrpc='2.0', method='notifications/initialized'),
                  dict(jsonrpc='2.0', id=2, method='tools/call', params=dict(name='get_user_state',
                       arguments=dict(metrics=['sleep'])))]
        stdin = ''.join(json.dumps(frame) + '\n' for frame in frames).encode()
        for code, expected in ((1, 'sleeping'), (0, 'not_sleeping')):
            with self.subTest(code=code):
                def reply(op, params, request_id):
                    metric = params['metric']
                    now = int(server.health_sync.clock()*1000)
                    result = dict(id=request_id, status='ok', metric=metric, firmware='OW20W2_11_A.92',
                                  deviceId='12345678-1234-1234-1234-123456789abc', watchReadAtMs=now)
                    if metric == 'wear':
                        return dict(result, rawCode=1, fieldPresent=True)
                    return dict(result, sleepMessageId=490, sleepEventStatus='event_received',
                                listenerSinceMs=now-5000, rawCode=code, fieldPresent=bool(code),
                                eventAtMs=now-3000, eventReceivedAtMs=now-2000, eventAgeMs=2000)

                with (patch.object(server.health_sync, 'store', store),
                      patch.object(server.health_sync, 'context', return_value=('rfcomm', 'test')),
                      patch.object(server.health_sync, 'request', side_effect=reply) as wire):
                    process = subprocess.run([sys.executable, str(adapter), '--port', str(self.http.server_port)],
                                             input=stdin, capture_output=True, check=True, timeout=10)
                response = [json.loads(line) for line in process.stdout.splitlines()][-1]['result']
                self.assertFalse(response['isError'])
                sleep = response['structuredContent']['sleep']
                self.assertEqual((sleep['value'], sleep['messageId'], sleep['fieldPresent']), (expected, 490, bool(code)))
                self.assertEqual([call.args[1]['metric'] for call in wire.call_args_list], ['wear', 'current_sleep'])

    def test_combined_state_flags_conflict_without_overwriting_native_sleep(self):
        wear = dict(value='worn', status='ok')
        sleep = dict(value='sleeping', status='ok', messageId=490, sourceAtMs=1794387548000)

        def observe(args):
            metric = args['metrics'][0]
            return {'results': {metric: wear if metric == 'wear' else sleep}}

        with (patch.object(server.health_sync, 'now', side_effect=observe),
              patch.object(server.health_sync, 'permitted_live_results', side_effect=lambda results: results),
              patch.object(server.data_access, 'user_state', return_value=dict(ok=True, tier=3, status='classified'))):
            single = server.call_tool('get_user_state', {'metrics':['sleep']})
            combined = server.call_tool('get_user_state', {'metrics':['sleep','accelerometer','gyroscope']})
        self.assertEqual(single['sleep'], combined['sleep'])
        self.assertEqual(combined['assessmentStatus'], 'conflicting_evidence')

    def test_stress_is_opt_in_and_archives_success_without_replaying_old_score(self):
        store = HealthHistory(self.temp.name)
        store.save_settings(dict(archiveEnabled=True, historyMetrics=['stress'], liveMetrics=['wear']))
        device = '12345678-1234-1234-1234-123456789abc'
        today = datetime.now(BEIJING).date()
        observed = day_start(today)*1000 + 1000
        wear = dict(value='worn', status='ok', deviceId=device)
        score = dict(ok=True, status='ok', score=41, requestId='fresh', source='stock_pressure_page',
                     observedAtMs=observed, receivedAtMs=observed+30)
        with (patch.object(server, 'health_history', store),
              patch.object(server.health_sync, 'context', return_value=('rfcomm', 'test')),
              patch.object(server.health_sync, 'now', return_value={'results':{'wear':wear}}),
              patch.object(server.health_sync, 'permitted_live_results', side_effect=lambda results: results),
              patch.object(server.data_access, 'measure_stress', side_effect=[score,dict(ok=False,status='no_fresh_result')]) as stress):
            self.assertNotIn('stress', server.call_tool('get_user_state', {}))
            stress.assert_not_called()
            actual = server.call_tool('get_user_state', {'metrics':['stress']})
            self.assertEqual(actual['stress']['archiveStatus'], 'saved')
            failed = server.call_tool('get_user_state', {'metrics':['stress']})
            self.assertFalse(failed['ok'])
            self.assertNotIn('score', failed['stress'])
            self.assertEqual(self.request('POST',value=dict(name='measure_stress',arguments={}))[0],400)
        reopened = HealthHistory(self.temp.name)
        self.assertEqual(reopened.latest_stress()['score'], 41)
        self.assertEqual(reopened.timeline(today.isoformat(),1,device)['stress'], [[observed,41]])
        request = dict(metrics=['stress'],start_date=today.isoformat(),end_date=today.isoformat(),view='timeline')
        day = reopened.query(request)['results']['stress']['days'][0]
        self.assertEqual((day['recordedSamples'], day['minimumScore'], day['maximumScore']), (1,41,41))
        self.assertEqual(day['observations'][0]['requestId'], 'fresh')
        reopened.save_settings(dict(archiveEnabled=True, historyMetrics=[], liveMetrics=['wear']))
        self.assertEqual(reopened.query(request)['results']['stress']['status'], 'disabled_by_settings')

    def test_dashboard_health_history_can_read_local_records_with_agent_history_off(self):
        store = HealthHistory(self.temp.name)
        store.save_settings(dict(archiveEnabled=False, historyMetrics=[], liveMetrics=[]))
        device = '12345678-1234-1234-1234-123456789abc'
        day = datetime.now(BEIJING).date()
        start = day_start(day)
        store.save_window(device, 'activity', start, start + 60,
                          dict(status='ok', rows=[[0, 0], [1, 4]]), start * 1000)
        view = f'/api/health-history?start_date={day}&end_date={day}&metric=activity&view=timeline&offset=0'
        with patch.object(server, 'health_history', store), patch.object(server.health_sync, 'now') as native:
            status, body = self.request('GET', view)
            self.assertEqual(status, 200)
            record = json.loads(body)['results']['activity']['days'][0]
            self.assertEqual([item['steps'] for item in record['minutes']], [0, 4])
            self.assertEqual(self.request('GET', view+'&offset=1')[0], 400)
            self.assertEqual(self.request('GET', view, extra={'Origin':'https://example.com'})[0], 403)
            status, body = self.request('POST', value=dict(name='get_watch_health_history', arguments={'metrics':['activity']}))
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['results']['activity']['status'], 'disabled_by_settings')
            native.assert_not_called()


if __name__ == '__main__':
    unittest.main()
