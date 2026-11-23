from datetime import date, timedelta
import json
import tempfile
import unittest
from unittest.mock import Mock

from health_history import HealthHistory, day_start
from health_sync import HealthSync, history_windows, normalize_reading, valid_window
from health_protocol import WEEK_SECONDS

DEVICE = '12345678-1234-1234-1234-123456789abc'
TODAY = date(2026, 11, 9)
NOW = day_start(TODAY) + 13*3600


class HealthSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = HealthHistory(self.temp.name)
        self.store.save_settings(dict(archiveEnabled=True, historyMetrics=['activity','historical_sleep','current_sleep'],
                                      liveMetrics=['activity','wear','current_sleep']))
        self.route = ('wifi', 'selection')
        self.request = Mock(side_effect=self.reply)
        self.sync = HealthSync(self.store, self.request, lambda: self.route, clock=lambda: NOW)

    def reply(self, op, params, request_id):
        metric = params['metric']
        value = dict(id=request_id, op=op, metric=metric, deviceId=DEVICE, status='ok',
                     firmware='OW20W2_11_A.92', watchReadAtMs=NOW*1000)
        if op == 'health_now':
            if metric == 'activity':
                value.update(steps=12, sourceAtMs=NOW*1000)
            else:
                value.update(rawCode=0, fieldPresent=False)
        else:
            start, end = params['from'], params['to']
            value.update(params, pages=1)
            if metric == 'activity':
                value.update(rows=[[0, 3], [1, 0]])
            else:
                value.update(first=start+1800, last=end-60, minutes=10, decoderVersion=1)
        return value

    def drain(self):
        for _ in range(40):
            self.sync.tick()
            if self.sync.worker:
                self.sync.worker.join(2)
                self.assertFalse(self.sync.worker.is_alive())
            if not self.sync.pending:
                break

    def test_backfill_is_bounded_and_reconnection_checks_recent_data(self):
        self.drain()
        self.assertEqual(self.sync.state()['status'], 'up_to_date')
        history = [call.args[1] for call in self.request.call_args_list if call.args[0] == 'health_history']
        self.assertTrue(history)
        self.assertTrue(all(NOW-WEEK_SECONDS <= item['from'] <= item['to'] <= NOW for item in history))
        self.route = ('rfcomm','reconnection')
        self.request.reset_mock()
        self.drain()
        histories = [call.args[1] for call in self.request.call_args_list if call.args[0] == 'health_history']
        self.assertEqual([item['metric'] for item in histories], ['activity','historical_sleep'])
        self.assertEqual(histories[0]['from'], NOW-1800)

    def test_native_heart_archives_second_offsets_and_retains_data_after_empty_recheck(self):
        def reply(op, params, request_id):
            result = self.reply(op, params, request_id)
            if op == 'health_now' and params['metric'] == 'activity':
                result['heartHistorySupported'] = True
            if params['metric'] == 'heart_rate':
                result.update(rows=[[0, 80, 4], [params['to']-params['from']+59, 93, 6]])
            return result

        self.request.side_effect = reply
        self.drain()
        start, end = NOW-3600, NOW-60
        timeline = self.store.timeline(TODAY.isoformat(), 1, DEVICE)
        self.assertIn([start*1000, 80], timeline['heart'])
        self.assertIn([(end+59)*1000, 93], timeline['heart'])
        self.store.save_window(DEVICE, 'heart_rate', start, end, dict(status='no_data'), NOW*1000+1)
        self.assertEqual(timeline['heart'], self.store.timeline(TODAY.isoformat(), 1, DEVICE)['heart'])
        args = dict(metrics=['heart_rate'], view='timeline', start_date=TODAY.isoformat(), end_date=TODAY.isoformat())
        self.assertEqual(self.store.query(args, today=TODAY)['results']['heart_rate']['status'], 'disabled_by_settings')
        points = self.store.query(args, today=TODAY, local_view=True)['results']['heart_rate']['days'][0]['samples']
        self.assertIn(dict(atMs=(end+59)*1000, bpm=93, nativeQuality=6), points)

    def test_unconfirmed_archive_timeout_defers_two_disjoint_windows(self):
        self.sync.token = self.route
        self.sync.device = DEVICE
        self.sync.next_state = NOW+120
        start, end = NOW-14400, NOW-60
        self.request.side_effect = lambda *args, **kwargs: dict(status='outcome_unknown_timeout')
        self.sync._window(self.route, 'heart_rate', start, end)
        first, second = self.sync.pending
        self.assertEqual((first[:3], second[:2], first[3]+60, second[3]),
                         (('window','heart_rate',start), ('window','heart_rate'), second[2], end))
        self.sync.tick()
        self.request.assert_called_once()
        self.assertFalse(self.store.windows(DEVICE,'heart_rate',start,end))
        self.sync.clock = lambda: NOW+11
        self.request.side_effect = lambda op, params, request_id: dict(
            self.reply(op, params, request_id), rows=[[0,72,1]])
        self.sync.tick()
        self.sync.worker.join(2)
        self.assertEqual(self.request.call_args.args[1]['from'], start)
        self.assertEqual(self.request.call_args.args[1]['to'], first[3])

    def test_offline_history_stays_local_and_is_idempotent(self):
        self.drain()
        self.route = None
        self.request.reset_mock()
        store = HealthHistory(self.temp.name)
        args = dict(metrics=['activity','historical_sleep','current_sleep','wear'],
                    start_date=TODAY.isoformat(),end_date=TODAY.isoformat())
        reply = store.query(args, today=TODAY)['results']
        self.assertEqual(reply['activity']['days'][0]['stepsFromRecords'], 3)
        self.assertEqual(reply['activity']['days'][0]['recordedMinutes'], 2)
        self.assertEqual(reply['historical_sleep']['days'][0]['value']['sleepMinutes'], 10)
        self.assertEqual(reply['current_sleep']['days'][0]['observationCounts'], {'unknown':1})
        self.assertEqual(reply['wear']['status'], 'disabled_by_settings')
        self.assertEqual(self.sync.now({'metrics':['current_sleep']})['results']['current_sleep']['status'], 'watch_offline')
        self.request.assert_not_called()
        start, end = day_start(TODAY), NOW
        response = self.reply('health_history', {'metric':'activity','from':start,'to':end}, 'test')
        store.save_window(DEVICE,'activity',start,end,response,NOW*1000)
        self.assertEqual(store.query(args,today=TODAY)['results']['activity']['days'][0]['recordedMinutes'],2)

    def test_activity_hours_use_the_whole_day_and_distinguish_missing_from_zero(self):
        start = day_start(TODAY)
        next_day = TODAY + timedelta(days=1)
        rows = [[minute, 1] for minute in range(101)] + [[1380, 0]]
        self.store.save_window(DEVICE, 'activity', start, start+86340,
                               dict(status='ok', rows=rows), (start+86400)*1000)
        self.store.save_window(DEVICE, 'activity', start+86400, start+86400,
                               dict(status='ok', rows=[[0, 9]]), (start+86460)*1000)
        args = dict(metrics=['activity'], start_date=TODAY.isoformat(), end_date=TODAY.isoformat(), view='timeline')
        first = self.store.query(args, today=next_day)['results']['activity']['days'][0]
        second = self.store.query(dict(args, offset=100), today=next_day)['results']['activity']['days'][0]
        self.assertEqual(first['hours'], second['hours'])
        self.assertEqual((len(first['minutes']), first['nextMinuteOffset'], len(second['minutes'])), (100, 100, 2))
        self.assertEqual((first['stepsFromRecords'], first['recordedMinutes']), (101, 102))
        self.assertEqual(len(first['hours']), 24)
        for hour, steps, count in ((0, 60, 60), (1, 41, 41), (2, None, 0), (23, 0, 1)):
            self.assertEqual(first['hours'][hour], dict(startAtMs=(start+hour*3600)*1000,
                                                       steps=steps, recordedMinutes=count))

    def test_sleep_read_preserves_default_zero_without_claiming_awake(self):
        reading = self.sync.now({'metrics':['current_sleep']})['results']['current_sleep']
        self.assertEqual((reading['value'],reading['rawCode'],reading['fieldPresent']), ('unknown',0,False))
        self.assertEqual(reading['status'],'state_unverified')
        self.request.assert_called_once()

    def test_sleep_event_reports_both_real_states_and_an_empty_listener(self):
        now = NOW*1000
        base = self.reply('health_now', {'metric':'current_sleep'}, 'test')
        base.update(sleepMessageId=490, listenerSinceMs=now-60000)
        for code, present, expected in ((1, True, 'sleeping'), (0, False, 'not_sleeping')):
            raw = dict(base, rawCode=code, fieldPresent=present, sleepEventStatus='event_received',
                       eventAtMs=now-4000, eventReceivedAtMs=now-3500, eventAgeMs=3500)
            result = normalize_reading(raw, 'current_sleep', 'test', now)
            self.assertEqual((result['status'], result['value'], result['sourceAtMs']), ('ok', expected, now-4000))
            raw['eventAtMs'] = now-61000
            self.assertEqual(normalize_reading(raw, 'current_sleep', 'test', now)['value'], 'unknown')
        for status in ('awaiting_event', 'listener_unavailable', 'invalid_event'):
            result = normalize_reading(dict(base, sleepEventStatus=status), 'current_sleep', 'test', now)
            self.assertEqual(result['value'], 'unknown')
            self.assertNotIn('rawCode', result)

    def test_sleep_archive_uses_event_times_and_keeps_cross_day_read_times(self):
        midnight = (day_start(TODAY) + 86400) * 1000
        first_event = midnight - 3*3600000
        second_event = midnight - 3600000
        first_read = second_event + 360000
        last_read = midnight + 3600000
        next_day = TODAY + timedelta(days=1)
        clock = Mock()
        self.sync.clock = clock

        def read(event_at, read_at, code):
            clock.return_value = read_at / 1000

            def reply(op, params, request_id):
                return dict(self.reply(op, params, request_id), watchReadAtMs=read_at,
                            sleepMessageId=490, listenerSinceMs=first_event-60000,
                            sleepEventStatus='event_received', eventAtMs=event_at,
                            eventReceivedAtMs=event_at+1000, eventAgeMs=read_at-event_at-1000,
                            rawCode=code, fieldPresent=bool(code))

            self.request.side_effect = reply
            self.assertEqual(self.sync.now({'metrics':['current_sleep']})['results']['current_sleep']['status'], 'ok')

        read(first_event, first_event+10000, 1)
        read(second_event, first_read, 0)
        read(second_event, last_read, 0)
        read(second_event, first_read+60000, 0)

        args = dict(metrics=['current_sleep'], start_date=TODAY.isoformat(), end_date=next_day.isoformat())
        summary = self.store.query(args, today=next_day)['results']['current_sleep']['days']
        self.assertEqual(summary[0]['observationCounts'], {'sleeping':1, 'not_sleeping':1})
        self.assertEqual(summary[0]['firstObservedAtMs'], first_event)
        self.assertEqual(summary[0]['lastObservedAtMs'], second_event)
        self.assertEqual(summary[0]['maximumGapMs'], second_event-first_event)
        self.assertEqual(summary[1]['observationCounts'], {})

        args.update(end_date=TODAY.isoformat(), view='timeline')
        observations = self.store.query(args, today=next_day)['results']['current_sleep']['days'][0]['observations']
        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[1]['eventReceivedAtMs'], second_event+1000)
        self.assertEqual(observations[1]['firstReadAtMs'], first_read)
        self.assertEqual(observations[1]['lastReadAtMs'], last_read)
        self.assertEqual(observations[1]['observedAtMs'], last_read)

    def test_revoking_a_live_metric_during_a_group_read_redacts_its_result(self):
        def revoke(op, params, request_id):
            if params['metric'] == 'current_sleep':
                self.store.save_settings(dict(self.store.settings(), liveMetrics=['current_sleep']))
            return self.reply(op, params, request_id)

        self.request.side_effect = revoke
        result = self.sync.now({'metrics':['wear','current_sleep']})['results']
        self.assertEqual(result['wear'], dict(metric='wear', value='unknown', status='disabled_by_settings'))
        self.assertEqual(result['current_sleep']['status'], 'state_unverified')

    def test_activity_backfill_overlaps_yesterday_and_revisits_empty_coverage(self):
        midnight = day_start(TODAY)
        yesterday = midnight - 86400
        self.sync.device = DEVICE
        self.sync.clock = lambda: midnight + 600
        checked_before_midnight = (midnight-60)*1000
        self.store.save_window(DEVICE, 'activity', yesterday, midnight-1200,
                               dict(status='ok', rows=[[0, 3]]), checked_before_midnight)
        self.sync._plan(False)
        self.assertIn(('window','activity',midnight-2940,midnight-60), self.sync.pending)

        self.store.save_window(DEVICE, 'activity', yesterday, midnight-60,
                               dict(status='no_data'), checked_before_midnight)
        self.sync.pending.clear()
        self.sync._plan(False)
        self.assertIn(('window','activity',midnight-2940,midnight-60), self.sync.pending)

        self.store.save_window(DEVICE, 'activity', midnight-2940, midnight-60,
                               dict(status='no_data'), midnight*1000)
        self.sync.pending.clear()
        self.sync._plan(False)
        self.assertFalse(any(job[1:2]==('activity',) and job[-1]==midnight-60 for job in self.sync.pending))

    def test_an_entire_empty_yesterday_is_rechecked_after_midnight(self):
        midnight = day_start(TODAY)
        yesterday = midnight - 86400
        self.sync.device = DEVICE
        self.sync.clock = lambda: midnight + 600
        self.store.save_window(DEVICE, 'activity', yesterday, midnight-60,
                               dict(status='no_data'), (midnight-60)*1000)
        self.sync._plan(False)
        self.assertIn(('window','activity',yesterday,midnight-60), self.sync.pending)

    def test_sleep_timeline_validates_and_survives_a_legacy_reply(self):
        _, windows = history_windows(NOW)
        metric, start, end = windows[0]
        raw = self.reply('health_history', {'metric':metric, 'from':start, 'to':end}, 'id')
        states = '11423' + '0'*21 + '2'
        raw.update(first=start, last=start+(len(states)-1)*60, minutes=5, decoderVersion=2, minuteStates=states)
        self.assertTrue(valid_window(raw, DEVICE, metric, start, end, 'id'))
        for invalid in (states[1:], '0'+states[1:], states.replace('4','9')):
            self.assertFalse(valid_window(dict(raw, minuteStates=invalid), DEVICE, metric, start, end, 'id'))
        self.store.save_window(DEVICE, metric, start, end, raw, NOW*1000)
        self.store.save_window(DEVICE, metric, start, end, dict(raw, decoderVersion=1), NOW*1000)
        args = dict(metrics=[metric], start_date=TODAY.isoformat(), end_date=TODAY.isoformat())
        summary = self.store.query(args, today=TODAY)['results'][metric]['days'][0]['value']
        self.assertNotIn('segments', summary)
        self.assertEqual(summary['stageMinutes'], dict(light=2, deep=2, rem=1, awake=1, gap=21))
        detail = self.store.query(dict(args, view='timeline'), today=TODAY)['results'][metric]['days'][0]['value']
        self.assertEqual([segment['stage'] for segment in detail['segments']], ['light','awake','deep','rem','gap','deep'])
        self.assertEqual(detail['segments'][-1]['endAtMs'], (raw['last']+60)*1000)

    def test_fragmented_sleep_timeline_fits_agent_limit_without_truncating_local_view(self):
        _, windows = history_windows(NOW)
        metric, start, end = windows[0]
        states = '12'*500
        raw = self.reply('health_history', {'metric':metric, 'from':start, 'to':end}, 'id')
        raw.update(first=start, last=start+(len(states)-1)*60, minutes=len(states),
                   decoderVersion=2, minuteStates=states)
        self.assertTrue(valid_window(raw, DEVICE, metric, start, end, 'id'))
        self.store.save_window(DEVICE, metric, start, end, raw, NOW*1000)
        args = dict(metrics=[metric], start_date=TODAY.isoformat(), end_date=TODAY.isoformat(), view='timeline')
        local = self.store.query(args, today=TODAY, local_view=True)['results'][metric]['days'][0]['value']
        combined = []
        offset = 0
        while offset is not None:
            response = self.store.query(dict(args, offset=offset), today=TODAY)
            self.assertLess(len(json.dumps(response).encode()), 65536)
            day = response['results'][metric]['days'][0]
            self.assertEqual(day['value']['sleepMinutes'], 1000)
            combined.extend(day['value']['segments'])
            offset = day['nextSegmentOffset']
        self.assertEqual(len(combined), 1000)
        self.assertEqual(combined, local['segments'])

    def test_reconnect_backfills_legacy_stages_after_the_watch_supports_them(self):
        self.drain()
        self.route = ('rfcomm', 'new-watch-build')

        def with_stages(op, params, request_id):
            reply = self.reply(op, params, request_id)
            if op == 'health_history' and params['metric'] == 'historical_sleep':
                first = reply['first']
                reply.update(decoderVersion=2, last=first+9*60, minuteStates='1'*10)
            return reply

        self.request.side_effect = with_stages
        self.drain()
        _, windows = history_windows(NOW)
        self.assertTrue(all(self.store.has_sleep_stages(DEVICE, end) for _, _, end in windows))

    def test_manual_stages_refresh_only_enriches_matching_archived_nights(self):
        self.drain()
        self.store.save_settings(dict(archiveEnabled=False, historyMetrics=[], liveMetrics=[]))
        _, windows = history_windows(NOW)
        metric, start, end = windows[0]
        query = dict(metrics=[metric], start_date=TODAY.isoformat(), end_date=TODAY.isoformat())
        previous = self.store.query(query, today=TODAY, local_view=True)['results'][metric]['days'][0]['value']

        def with_stages(op, params, request_id):
            reply = self.reply(op, params, request_id)
            if params['from'] == start:
                first, last = reply['first'], reply['last']
                reply.update(decoderVersion=2, minuteStates='1'*9+'0'*((last-first)//60-9)+'1')
            return reply

        self.request.reset_mock(side_effect=True)
        self.request.side_effect = with_stages
        result = self.sync.fill_sleep_stages({'device_id':DEVICE})
        self.assertEqual(result['days'][0], dict(date=TODAY.isoformat(), status='updated'))
        self.assertTrue(all(call.args[0] == 'health_history' for call in self.request.call_args_list))
        value = self.store.query(dict(query, view='timeline'), today=TODAY,
                                 local_view=True)['results'][metric]['days'][0]['value']
        self.assertEqual(value['stageMinutes']['light'], 10)
        self.assertEqual({key:value[key] for key in previous if key != 'decoderVersion'},
                         {key:previous[key] for key in previous if key != 'decoderVersion'})
        self.assertFalse(self.store.settings()['archiveEnabled'])

    def test_expired_and_route_switched_replies_cannot_be_archived(self):
        result = self.reply('health_now', {'metric':'wear'}, 'id')
        self.assertEqual(normalize_reading(result,'wear','id',NOW*1000+300000)['status'],'invalid_result')
        self.request.side_effect = lambda *args, **kwargs: self._switch_reply(*args, **kwargs)
        reading = self.sync.now({'metrics':['wear']})['results']['wear']
        self.assertEqual(reading['status'],'connection_changed')
        self.assertFalse(self.store.database.exists())

    def _switch_reply(self, *args, **kwargs):
        self.route = ('rfcomm','new')
        return self.reply(*args, **kwargs)

    def test_mismatched_windows_and_duplicate_minutes_rejected(self):
        activity, sleep = history_windows(NOW)
        self.assertGreaterEqual(min(window[1] for window in activity+sleep), NOW-WEEK_SECONDS)
        metric, start, end = activity[0]
        reply = self.reply('health_history', {'metric':metric,'from':start,'to':end}, 'id')
        self.assertTrue(valid_window(reply, DEVICE, metric, start, end, 'id'))
        self.assertFalse(valid_window(reply, DEVICE, metric, start, end, 'old-id'))
        reply['rows'] = [[0, 5], [0, 9]]
        self.assertFalse(valid_window(reply, DEVICE, metric, start, end, 'id'))
