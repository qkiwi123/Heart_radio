"""Bounded native health reads and connection-driven computer archiving."""

from collections import deque
from datetime import timedelta
import sqlite3
import threading
import time
import uuid

from health_history import DEVICE, day_start, local_day, metric_list
from health_protocol import NOW_METRICS, WEEK_SECONDS

ACTIVITY_PERIOD = 1800
STATE_PERIOD = 120
RETRY_PERIOD = 300
HEART_WINDOW = 4 * 3600
HEART_REFRESH = 3600


def number(value, low, high):
    return type(value) is int and low <= value <= high


def history_windows(now):
    """Inclusive minute ranges, bounded by a rolling seven days."""
    first = ((now-WEEK_SECONDS+59)//60)*60
    last = now//60*60
    today = local_day(last)
    activity, sleep = [], []
    for offset in range(8):
        day = day_start(today - timedelta(days=offset))
        start, end = max(first, day), min(last, day+86340)
        if start <= end:
            activity.append(('activity', start, end))
        if first <= day-21600 and day+43200 <= last:
            sleep.append(('historical_sleep', day-21600, day+43200))
    return activity, sleep


def normalize_reading(raw, metric, request_id, received):
    result = dict(metric=metric, source='watch_native', requestId=request_id, value='unknown',
                  status=raw.get('status', 'invalid_result'), receivedAtMs=received)
    if raw.get('status') != 'ok':
        return result
    if (raw.get('id') != request_id or raw.get('metric') != metric
            or raw.get('firmware') != 'OW20W2_11_A.92'
            or not isinstance(raw.get('deviceId'), str) or not DEVICE.fullmatch(raw['deviceId'])
            or not number(raw.get('watchReadAtMs'), received-120000, received+120000)):
        return dict(result, status='invalid_result')
    result.update(deviceId=raw['deviceId'], observedAtMs=raw['watchReadAtMs'])
    if metric == 'activity':
        result['heartHistorySupported'] = raw.get('heartHistorySupported') is True
        steps, source = raw.get('steps'), raw.get('sourceAtMs')
        if not number(steps, 0, 2**31-1):
            return dict(result, status='no_data')
        # The counter is day-based. Keep the native timestamp separate from receipt time.
        valid_source = number(source, 946684800000, raw['watchReadAtMs']+60000)
        return dict(result, value=steps, sourceAtMs=source if valid_source else None,
                    sourceAgeVerified=False)
    if metric == 'current_sleep' and 'sleepMessageId' in raw:
        event_status = raw.get('sleepEventStatus')
        since = raw.get('listenerSinceMs')
        read_at = raw['watchReadAtMs']
        if raw['sleepMessageId'] != 490 or event_status not in (
                'listener_unavailable', 'awaiting_event', 'invalid_event', 'event_received'):
            return dict(result, status='invalid_result')
        result.update(messageId=490, sleepEventStatus=event_status, sourceAgeVerified=False)
        if event_status == 'listener_unavailable':
            return dict(result, status=event_status)
        if not number(since, 946684800000, read_at):
            return dict(result, status='invalid_result')
        result['listenerSinceMs'] = since
        if event_status != 'event_received':
            return dict(result, status='no_data' if event_status == 'awaiting_event' else 'invalid_result')
        code, present = raw.get('rawCode'), raw.get('fieldPresent')
        event_at, event_received, age = (raw.get(key) for key in ('eventAtMs', 'eventReceivedAtMs', 'eventAgeMs'))
        if (not number(code, 0, 1) or type(present) is not bool or (code == 1 and not present)
                or not number(event_at, since//1000*1000, read_at+60000)
                or not number(event_received, since, read_at) or not number(age, 0, 2**53-1)):
            return dict(result, status='invalid_result')
        return dict(result, value='sleeping' if code else 'not_sleeping', rawCode=code,
                    fieldPresent=present, sourceAtMs=event_at, eventReceivedAtMs=event_received,
                    eventAgeMs=age)
    code, present = raw.get('rawCode'), raw.get('fieldPresent')
    if not number(code, 0, 255) or type(present) is not bool or (not present and code != 0):
        return dict(result, status='invalid_result')
    result.update(rawCode=code, fieldPresent=present, sourceAgeVerified=False)
    if metric == 'wear' and code in (0, 1):
        result.update(value='worn' if code else 'off_wrist')
    else:
        result.update(status='state_unverified')
    return result


def valid_window(raw, device, metric, start, end, request_id):
    if (raw.get('id') != request_id or raw.get('deviceId') != device or raw.get('metric') != metric
            or raw.get('firmware') != 'OW20W2_11_A.92' or raw.get('from') != start or raw.get('to') != end):
        return False
    if raw.get('status') == 'no_data':
        return True
    if raw.get('status') != 'ok' or not number(raw.get('pages'), 1, 150):
        return False
    size = (end-start)//60 + 1
    if metric == 'activity':
        rows = raw.get('rows')
        return (isinstance(rows, list) and 1 <= len(rows) <= min(size, 650)
                and all(isinstance(row, list) and len(row) == 2 and number(row[0], 0, size-1)
                        and number(row[1], 0, 2**31-1) for row in rows)
                and all(a[0] < b[0] for a,b in zip(rows,rows[1:])))
    if metric == 'heart_rate':
        rows = raw.get('rows')
        return (isinstance(rows, list) and 1 <= len(rows) <= 650
                and all(isinstance(row, list) and len(row) == 3 and number(row[0], 0, end-start+59)
                        and number(row[1], 1, 255) and number(row[2], 0, 255) for row in rows)
                and all(a[0] < b[0] for a, b in zip(rows, rows[1:])))
    first, last = raw.get('first'), raw.get('last')
    if not (number(first, start, end) and number(last, first, end)
            and first % 60 == last % 60 == 0 and number(raw.get('minutes'), 1, (last-first)//60+1)):
        return False
    if raw.get('decoderVersion') == 1:
        return True
    states = raw.get('minuteStates')
    return (raw.get('decoderVersion') == 2 and isinstance(states, str) and len(states) == (last-first)//60+1
            and states[0] in '123' and states[-1] in '123' and all(code in '01234567' for code in states)
            and sum(code in '123' for code in states) == raw['minutes'])


class HealthSync:
    def __init__(self, store, request, context, connection=None, clock=time.time):
        self.store, self.request, self.context, self.connection, self.clock = store, request, context, connection, clock
        self.wire = threading.Lock()
        self.lock = threading.RLock()
        self.worker = None
        self.stopped = False
        self.token = None
        self.device = None
        self.pending = deque()
        self.archive_resume_at = 0
        self.next_activity = self.next_state = 0
        self.had_error = False
        self.checking = False
        self.sleep_stages_supported = False
        self.heart_history_supported = False
        self.summary = dict(status='disabled', lastSelfCheckAtMs=None, lastArchiveAtMs=None, lastError=None)

    def state(self):
        with self.lock:
            return dict(self.summary, pending=len(self.pending))

    def stop(self):
        with self.lock:
            self.stopped = True

    def tick(self):
        try:
            enabled = self.store.settings()['archiveEnabled']
        except (OSError, ValueError):
            with self.lock:
                self.summary.update(status='settings_error', lastError='settings_error')
            return
        token = self.context() if enabled else None
        with self.lock:
            if self.stopped or self.worker and self.worker.is_alive():
                return
            if not enabled:
                self.token = None
                self.pending.clear()
                self.summary['status'] = 'disabled'
                return
            if token is None:
                self.summary['status'] = 'waiting_for_interaction'
                return
            now = self.clock()
            if token != self.token:
                self.token, self.device = token, None
                self.archive_resume_at = 0
                self.sleep_stages_supported = False
                self.heart_history_supported = False
                self.pending = deque([('now', metric) for metric in NOW_METRICS] + [('plan', True)])
                self.had_error = False
                self.checking = True
                self.next_activity, self.next_state = now+ACTIVITY_PERIOD, now+STATE_PERIOD
                self.summary.update(status='checking', lastError=None)
            elif not self.pending and now >= self.next_activity:
                self.pending.extend([('now', 'activity'), ('plan', False)])
                self.next_activity = now+ACTIVITY_PERIOD
                self.had_error = False
                self.summary['lastError'] = None
            elif now >= self.next_state:
                self.pending.extendleft([('now', 'current_sleep'), ('now', 'wear')])
                self.next_state = now+STATE_PERIOD
            if not self.pending or now < self.archive_resume_at:
                return
            if not self.wire.acquire(blocking=False):
                return
            job = self.pending.popleft()
            self.worker = threading.Thread(target=self._work, args=(job,token), name='watch-health-sync', daemon=True)
            self.worker.start()

    def _work(self, job, token):
        try:
            kind, *args = job
            if kind == 'plan':
                self._plan(*args)
            elif kind == 'now':
                result = self._read(args[0], token, background=True)
                if result.get('deviceId'):
                    self.device = result['deviceId']
                if args[0] == 'activity' and 'heartHistorySupported' in result:
                    self.heart_history_supported = result['heartHistorySupported']
                if result['status'] not in ('ok', 'state_unverified', 'no_data'):
                    self._error(result['status'])
            else:
                self._window(token, *args)
        except (OSError, ValueError, sqlite3.Error):
            self._error('storage_or_read_error')
        finally:
            self.wire.release()
            with self.lock:
                if self.context() == token and not self.stopped and not self.pending:
                    self.summary['status'] = 'partial' if self.had_error else 'up_to_date'
                    if self.checking:
                        self.checking = False
                        self.summary['lastSelfCheckAtMs'] = int(self.clock()*1000)

    def _error(self, status):
        with self.lock:
            self.had_error = True
            self.summary['lastError'] = status
            self.next_activity = min(self.next_activity, self.clock()+RETRY_PERIOD)

    def _plan(self, recheck):
        device = self.device
        if device is None:
            return
        now = int(self.clock())
        today_at_ms = day_start(local_day(now))*1000
        activity, sleep = history_windows(now)
        windows = []
        for index, (metric, start, end) in enumerate(activity):
            rows = self.store.windows(device, metric, start, end)
            if index == 1:
                # Yesterday's empty windows can acquire data after midnight.
                rows = [row for row in rows if row['status'] != 'no_data' or row['checked'] >= today_at_ms]
            through = self.store.complete_through(rows, start)
            resume = max(start, min(through + 60, end) - (ACTIVITY_PERIOD if index < 2 else 0))
            if through >= end and index != 0:
                if index > 1:
                    continue
                checked_today = [row for row in rows if row['checked'] >= today_at_ms]
                if self.store.complete_through(checked_today, resume) >= end:
                    continue
            windows.append(('window', metric, resume, end))
        for index, (metric, start, end) in enumerate(sleep):
            rows = self.store.windows(device, metric, start, end)
            previous = next((row for row in rows if row['start'] == start and row['end'] == end), None)
            recent = previous is not None and previous['checked'] >= (now-6*3600)*1000
            if (index == 0 and recheck) or previous is None or not recent and (index < 2 or previous['status'] == 'no_data'):
                windows.insert(index*2+1, ('window', metric, start, end))
        if self.heart_history_supported:
            # Revisit the latest hour so delayed device records can be filed. Keep bounded,
            # day-aligned older windows so a later connection resumes where it left off.
            latest = now//60*60 - 60
            cutoff = max(latest - HEART_REFRESH + 60, ((now-WEEK_SECONDS+59)//60)*60)
            windows.insert(1, ('window', 'heart_rate', cutoff, latest))
            for _, first, last in activity:
                end = min(last, cutoff-60)
                for start in range(first, end+1, HEART_WINDOW):
                    stop = min(end, start+HEART_WINDOW-60)
                    coverage = self.store.windows(device, 'heart_rate', start, stop)
                    if first >= today_at_ms//1000-86400:
                        coverage = [row for row in coverage
                                    if row['status'] != 'no_data' or row['checked'] >= today_at_ms]
                    through = self.store.complete_through(coverage, start)
                    if through < stop:
                        windows.append(('window', 'heart_rate', through+60, stop))
        with self.lock:
            self.pending.extend(windows)
            if windows:
                self.summary['status'] = 'syncing'

    def _can_archive(self, token, revision):
        return (not self.stopped and self.context() == token and self.store.revision == revision
                and self.store.settings()['archiveEnabled'])

    def _backfill_sleep_stages(self):
        self.sleep_stages_supported = True
        _, windows = history_windows(int(self.clock()))
        for metric, start, end in windows:
            if self.store.has_sleep_stages(self.device, end):
                continue
            prior = self.store.windows(self.device, metric, start, end)
            if any(row['start'] == start and row['end'] == end and row['status'] == 'ok' for row in prior):
                job = ('window', metric, start, end)
                with self.lock:
                    if job not in self.pending:
                        self.pending.append(job)

    def _window(self, token, metric, start, end):
        if self.device is None:
            return
        with self.store.lock:
            revision = self.store.revision
            if not self._can_archive(token, revision):
                return
        oldest = ((int(self.clock())-WEEK_SECONDS+59)//60)*60
        if start < oldest:
            if metric == 'historical_sleep' or end < oldest:
                return
            start = oldest
        identity = uuid.uuid4().hex
        raw = self.request('health_history', {'metric':metric, 'from':start, 'to':end}, request_id=identity)
        received = int(self.clock()*1000)
        with self.store.lock:
            if not self._can_archive(token, revision):
                return
            if valid_window(raw, self.device, metric, start, end, identity):
                self.store.save_window(self.device, metric, start, end, raw, received)
                if (metric == 'historical_sleep' and raw['status'] == 'ok'
                        and raw['decoderVersion'] == 2 and not self.sleep_stages_supported):
                    self._backfill_sleep_stages()
            elif (metric in ('activity', 'heart_rate') and raw.get('status') in
                  ('split_required', 'request_timeout', 'outcome_unknown_timeout')
                  and end-start >= (900 if metric == 'heart_rate' else 3600)):
                middle = ((start+end)//120)*60
                with self.lock:
                    if raw['status'] == 'outcome_unknown_timeout':
                        # Allow a full watch read budget (9 s) after an unconfirmed reply.
                        # NativeHealth also serializes reads and rejects overlapping work.
                        self.archive_resume_at = self.clock() + 10
                    self.pending.extendleft([('window', metric, middle+60, end), ('window', metric, start, middle)])
                return
            else:
                self._error(raw.get('status') if raw.get('status') not in (None,'ok') else 'invalid_result')
                return
        with self.lock:
            self.summary['lastArchiveAtMs'] = received

    def _read(self, metric, token, background=False):
        with self.store.lock:
            revision = self.store.revision
            settings = self.store.settings()
            if background and not self._can_archive(token, revision):
                return dict(metric=metric, value='unknown', status='archive_disabled')
            if not background and metric not in settings['liveMetrics']:
                return dict(metric=metric, value='unknown', status='disabled_by_settings')
        identity = uuid.uuid4().hex
        raw = self.request('health_now', {'metric':metric}, request_id=identity)
        received = int(self.clock()*1000)
        if self.context() != token:
            return dict(metric=metric, value='unknown', status='connection_changed')
        if raw.get('status') == 'unknown_operation':
            return dict(metric=metric, value='unknown', status='watch_update_required')
        result = normalize_reading(raw, metric, identity, received)
        with self.store.lock:
            if not background and metric not in self.store.settings()['liveMetrics']:
                return dict(metric=metric, value='unknown', status='disabled_by_settings')
            if result.get('deviceId') and result['status'] in ('ok','state_unverified') and self._can_archive(token, revision):
                try:
                    self.store.save_observation(result['deviceId'], metric, result)
                except (OSError, sqlite3.Error):
                    result['archiveStatus'] = 'storage_error'
                    self._error('storage_error')
                else:
                    with self.lock:
                        self.summary['lastArchiveAtMs'] = received
        return result

    def permitted_live_results(self, results):
        with self.store.lock:
            enabled = self.store.settings()['liveMetrics']
            return {metric: result if metric in enabled else
                    dict(metric=metric, value='unknown', status='disabled_by_settings')
                    for metric, result in results.items()}

    def _connection_unavailable(self):
        state = self.connection() if self.connection is not None else {}
        return ('watch_offline' if not state.get('connected') else
                'interaction_disabled' if not state.get('agentEnabled') else 'interaction_sync_pending')

    def fill_sleep_stages(self, arguments):
        """Refresh only existing sleep summaries for one device on explicit local request."""
        if not isinstance(arguments, dict) or set(arguments) != {'device_id'}:
            raise ValueError('Expected a device ID')
        device = arguments['device_id']
        if not isinstance(device, str) or not DEVICE.fullmatch(device):
            raise ValueError('Invalid device identity')
        token = self.context()
        if token is None:
            return dict(status=self._connection_unavailable(), days=[])
        if not self.wire.acquire(timeout=1.5):
            return dict(status='busy', days=[])
        try:
            now = int(self.clock())
            oldest = ((now-WEEK_SECONDS+59)//60)*60
            today = local_day(now)
            metric = 'historical_sleep'
            result = self.store.query(dict(metrics=[metric], device_id=device,
                                           start_date=local_day(oldest).isoformat(), end_date=today.isoformat()),
                                      today=today, local_view=True)
            days = []
            for entry in reversed(result['results'][metric].get('days', [])):
                night = entry['value']
                if not night or night['decoderVersion'] != 1:
                    continue
                start = max(oldest, night['fromAtMs']//1000)
                end = night['toAtMs']//1000
                status = 'outside_window'
                if start <= night['startAtMs']//1000 and end <= now:
                    if self.stopped or self.context() != token:
                        return dict(status='connection_changed', days=days)
                    identity = uuid.uuid4().hex
                    raw = self.request('health_history', {'metric':metric, 'from':start, 'to':end}, request_id=identity)
                    if self.stopped or self.context() != token:
                        return dict(status='connection_changed', days=days)
                    if raw.get('deviceId') and raw['deviceId'] != device:
                        return dict(status='different_device', days=days)
                    status = raw.get('status', 'invalid_result')
                    if status == 'ok':
                        status = 'invalid_result'
                        if valid_window(raw, device, metric, start, end, identity):
                            status = ('watch_update_required' if raw['decoderVersion'] == 1
                                      else self.store.enrich_sleep_night(device, entry['date'], raw))
                days.append(dict(date=entry['date'], status=status))
            return dict(status='complete', days=days)
        finally:
            self.wire.release()

    def now(self, arguments):
        if not isinstance(arguments, dict) or set(arguments) != {'metrics'}:
            raise ValueError('Expected health metrics')
        metrics = metric_list(arguments['metrics'], NOW_METRICS)
        token = self.context()
        unavailable = None
        if token is None:
            unavailable = self._connection_unavailable()
        elif not self.wire.acquire(timeout=1.5):
            unavailable = 'busy'
        if unavailable:
            return dict(ok=False, complete=False, source='watch_native',
                        results={metric:dict(value='unknown',status=unavailable) for metric in metrics})
        try:
            results = {}
            for metric in metrics:
                results[metric] = (self._read(metric, token) if self.context() == token
                                   else dict(value='unknown', status='connection_changed'))
            results = self.permitted_live_results(results)
            return dict(ok=any(item['status'] in ('ok','state_unverified') or item.get('sleepEventStatus') == 'awaiting_event'
                               for item in results.values()),
                        complete=all(item['status'] == 'ok' for item in results.values()),
                        source='watch_native', results=results)
        finally:
            self.wire.release()
