"""Computer-only storage and queries for native watch health records."""

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
import json
from itertools import groupby
from pathlib import Path
import re
import sqlite3
import threading

from health_protocol import HISTORY_METRICS, NOW_METRICS

BEIJING = timezone(timedelta(hours=8))
DEVICE = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
DEFAULTS = dict(archiveEnabled=False, historyMetrics=[], liveMetrics=list(NOW_METRICS))
SCHEMA = '''
CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, seen INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS observations (
    device TEXT, metric TEXT, identity TEXT, observed INTEGER, payload TEXT,
    PRIMARY KEY(device, metric, identity)
);
CREATE INDEX IF NOT EXISTS observations_time ON observations(device, metric, observed);
CREATE TABLE IF NOT EXISTS activity (
    device TEXT, minute INTEGER, steps INTEGER, PRIMARY KEY(device, minute)
);
CREATE TABLE IF NOT EXISTS heart_rate (
    device TEXT, measured INTEGER, bpm INTEGER, quality INTEGER, received INTEGER,
    PRIMARY KEY(device, measured)
);
CREATE TABLE IF NOT EXISTS nights (
    device TEXT, day TEXT, payload TEXT, PRIMARY KEY(device, day)
);
CREATE TABLE IF NOT EXISTS coverage (
    device TEXT, metric TEXT, start INTEGER, end INTEGER, status TEXT, checked INTEGER,
    PRIMARY KEY(device, metric, start, end)
);
'''


def metric_list(value, allowed, minimum=1):
    if (not isinstance(value, list) or not minimum <= len(value) <= len(allowed)
            or any(type(item) is not str or item not in allowed for item in value)
            or len(value) != len(set(value))):
        raise ValueError('Invalid health metrics')
    return value


def validate_settings(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS) or type(value['archiveEnabled']) is not bool:
        raise ValueError('Invalid health settings')
    return dict(archiveEnabled=value['archiveEnabled'],
                historyMetrics=metric_list(value['historyMetrics'], HISTORY_METRICS, 0),
                liveMetrics=metric_list(value['liveMetrics'], NOW_METRICS, 0))


def day_start(day):
    return int(datetime.combine(day, time(), BEIJING).timestamp())


def local_day(epoch):
    return datetime.fromtimestamp(epoch, BEIJING).date()


def sleep_stages(states, start_at_ms):
    stage_codes = {'1': 'light', '2': 'deep', '3': 'rem'}
    totals = dict(light=0, deep=0, rem=0, awake=0, gap=0)
    segments = []
    cursor = start_at_ms
    for stage, minutes in groupby(states, key=lambda code: stage_codes.get(code)):
        run = list(minutes)
        # The stock report counts short interruptions or an explicit awake run as WASO.
        label = stage or ('awake' if len(run) <= 20 or all(code == '4' for code in run) else 'gap')
        end = cursor + len(run)*60000
        segments.append(dict(stage=label, startAtMs=cursor, endAtMs=end))
        totals[label] += len(run)
        cursor = end
    return totals, segments


class HealthHistory:
    def __init__(self, root):
        self.directory = Path(root) / 'runs' / 'health-history'
        self.database = self.directory / 'health.sqlite3'
        self.settings_path = self.directory / 'settings.json'
        self.lock = threading.RLock()
        self.revision = 0
        self.initialized = False

    def settings(self):
        with self.lock:
            try:
                return validate_settings(json.loads(self.settings_path.read_text(encoding='utf-8')))
            except FileNotFoundError:
                return validate_settings(DEFAULTS)

    def save_settings(self, value):
        value = validate_settings(value)
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(value), encoding='utf-8')
            temporary.replace(self.settings_path)
            self.revision += 1
        return value

    @contextmanager
    def connection(self, write=False):
        if write:
            self.directory.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.database, timeout=2)
        else:
            db = sqlite3.connect(self.database.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            if write and not self.initialized:
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version not in (0, 1):
                    raise sqlite3.DatabaseError('Unsupported health store version')
                db.execute('PRAGMA journal_mode=WAL')
                db.executescript(SCHEMA + 'PRAGMA user_version=1;')
                self.initialized = True
            yield db
        finally:
            db.close()

    def save_observation(self, device, metric, reading):
        received = reading['receivedAtMs']
        source = reading.get('sourceAtMs')
        read_at = reading['observedAtMs']
        identity = (f'{source}:{reading["value"]}' if source is not None
                    else reading['requestId'])
        with self.lock, self.connection(write=True) as db, db:
            self._observe_device(db, device, received)
            if metric == 'current_sleep' and source is not None:
                row = db.execute('SELECT payload FROM observations WHERE device=? AND metric=? AND identity=?',
                                 (device, metric, identity)).fetchone()
                previous = json.loads(row['payload']) if row else reading
                previous_read = previous['observedAtMs']
                first_read = min(read_at, previous.get('firstReadAtMs', previous_read))
                last_read = max(read_at, previous.get('lastReadAtMs', previous_read))
                latest = reading if received >= previous['receivedAtMs'] else previous
                reading = dict(latest, firstReadAtMs=first_read, lastReadAtMs=last_read)
            db.execute('''INSERT INTO observations VALUES (?,?,?,?,?)
                ON CONFLICT(device,metric,identity) DO UPDATE SET payload=excluded.payload''',
                (device, metric, identity, source if source is not None else read_at, json.dumps(reading)))

    def record_stress(self, device, reading, revision):
        """Archive only a validated fresh pressure score from the explicit point test."""
        with self.lock:
            if self.revision != revision or not self.settings()['archiveEnabled']:
                return 'archive_disabled'
            if not isinstance(device, str) or not DEVICE.fullmatch(device):
                return 'device_unconfirmed'
            if (reading.get('ok') is not True or reading.get('status') != 'ok'
                    or reading.get('source') != 'stock_pressure_page'
                    or type(reading.get('score')) is not int or not 1 <= reading['score'] <= 100):
                return 'invalid_result'
            self.save_observation(device, 'stress', dict(reading, deviceId=device, value=reading['score']))
            return 'saved'

    def latest_stress(self):
        if not self.database.exists():
            return None
        with self.connection() as db:
            row = db.execute("SELECT payload FROM observations WHERE metric='stress' ORDER BY observed DESC,identity DESC LIMIT 1").fetchone()
            return json.loads(row['payload']) if row else None

    @staticmethod
    def _observe_device(db, device, received):
        db.execute('INSERT INTO devices VALUES (?,?) ON CONFLICT(id) DO UPDATE SET seen=MAX(seen,excluded.seen)',
                   (device, received))

    def save_window(self, device, metric, start, end, result, received):
        status = result['status']
        with self.lock, self.connection(write=True) as db, db:
            self._observe_device(db, device, received)
            if status == 'ok' and metric == 'activity':
                db.executemany('INSERT INTO activity VALUES (?,?,?) ON CONFLICT(device,minute) DO UPDATE SET steps=excluded.steps',
                               ((device, start + offset * 60, steps) for offset, steps in result['rows']))
            elif status == 'ok' and metric == 'heart_rate':
                db.executemany('''INSERT INTO heart_rate VALUES (?,?,?,?,?)
                    ON CONFLICT(device,measured) DO UPDATE SET bpm=excluded.bpm,quality=excluded.quality,
                    received=excluded.received WHERE excluded.received>=heart_rate.received''',
                    ((device, start + offset, bpm, quality, received) for offset, bpm, quality in result['rows']))
            elif status == 'ok':
                night = dict(startAtMs=result['first']*1000, endAtMs=result['last']*1000, sleepMinutes=result['minutes'],
                             fromAtMs=start*1000, toAtMs=end*1000, decoderVersion=result['decoderVersion'],
                             receivedAtMs=received, timezone='Asia/Shanghai')
                day = local_day(end).isoformat()
                if result['decoderVersion'] == 2:
                    night['stageMinutes'], night['segments'] = sleep_stages(result['minuteStates'], night['startAtMs'])
                old = db.execute('SELECT payload FROM nights WHERE device=? AND day=?', (device, day)).fetchone()
                if not old or json.loads(old[0])['decoderVersion'] <= night['decoderVersion']:
                    db.execute('INSERT INTO nights VALUES (?,?,?) ON CONFLICT(device,day) DO UPDATE SET payload=excluded.payload',
                               (device, day, json.dumps(night)))
            db.execute('''INSERT INTO coverage VALUES (?,?,?,?,?,?)
                ON CONFLICT(device,metric,start,end) DO UPDATE SET status=excluded.status,checked=excluded.checked''',
                (device, metric, start, end, status, received))

    def windows(self, device, metric, start, end):
        if not self.database.exists():
            return []
        with self.connection() as db:
            return db.execute('''SELECT start,end,status,checked FROM coverage
                WHERE device=? AND metric=? AND end>=? AND start<=? ORDER BY start,end''',
                (device, metric, start, end)).fetchall()

    def has_sleep_stages(self, device, end):
        if not self.database.exists():
            return False
        with self.connection() as db:
            row = db.execute('SELECT payload FROM nights WHERE device=? AND day=?',
                             (device, local_day(end).isoformat())).fetchone()
        return row is not None and 'segments' in json.loads(row[0])

    def enrich_sleep_night(self, device, day, result):
        """Add validated stages to an existing summary with matching sleep boundaries."""
        with self.lock, self.connection(write=True) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM nights WHERE device=? AND day=?', (device, day)).fetchone()
            if row is None:
                return 'summary_changed'
            night = json.loads(row['payload'])
            if (night['startAtMs'], night['endAtMs'], night['sleepMinutes']) != (
                    result['first']*1000, result['last']*1000, result['minutes']):
                return 'summary_changed'
            if night['decoderVersion'] != 1:
                return 'already_complete'
            totals, segments = sleep_stages(result['minuteStates'], night['startAtMs'])
            night.update(decoderVersion=2, stageMinutes=totals, segments=segments)
            db.execute('UPDATE nights SET payload=? WHERE device=? AND day=?', (json.dumps(night), device, day))
            return 'updated'

    @staticmethod
    def complete_through(windows, start):
        """Coverage is query completeness; sparse activity minutes remain sparse."""
        end = start - 60
        for row in windows:
            if row['status'] in ('ok', 'no_data') and row['start'] <= end + 60:
                end = max(end, row['end'])
        return end

    @staticmethod
    def _has_heart_rate(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='heart_rate'").fetchone() is not None

    def timeline(self, end, days, device):
        """Local dashboard range. Sleep is clipped by the UI at the shared midnight boundaries."""
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', end) or days not in (1, 7):
            raise ValueError('Invalid timeline date or span')
        anchor = date.fromisoformat(end)
        if not date(2000, 1, days) <= anchor <= datetime.now(BEIJING).date():
            raise ValueError('Invalid timeline range')
        start = anchor - timedelta(days=days-1)
        if device and not DEVICE.fullmatch(device):
            raise ValueError('Invalid device identity')
        lower, upper = day_start(start), day_start(anchor + timedelta(days=1))
        result = dict(startDate=start.isoformat(), endDate=end, startAtMs=lower*1000, endAtMs=upper*1000,
                      availableDevices=[], deviceId=None, heart=[], sleep=[], activity=[], activityMinutes=[], stress=[])
        if not self.database.exists():
            return result
        with self.connection() as db:
            devices = [row['id'] for row in db.execute('SELECT id FROM devices ORDER BY seen DESC,id')]
            device = device or (devices[0] if devices else None)
            result.update(availableDevices=devices, deviceId=device if device in devices else None)
            if device not in devices:
                return result
            if self._has_heart_rate(db):
                rows = db.execute('''SELECT measured,bpm FROM heart_rate WHERE device=? AND measured>=?
                    AND measured<? ORDER BY measured''', (device, lower, upper))
                result['heart'] = [[row['measured']*1000, row['bpm']] for row in rows]
            rows = db.execute('SELECT payload FROM nights WHERE device=? AND day>=? AND day<=? ORDER BY day',
                              (device, start.isoformat(), local_day(upper).isoformat()))
            result['sleep'] = [night for row in rows if (night := json.loads(row['payload']))['startAtMs'] < upper*1000
                               and night['endAtMs']+60000 > lower*1000]
            hours = {}
            rows = db.execute('''SELECT minute,steps FROM activity WHERE device=? AND minute>=? AND minute<?
                ORDER BY minute''', (device, lower, upper))
            for row in rows:
                at, steps = row['minute'], row['steps']
                start_at = (at//3600)*3600000
                bucket = hours.setdefault(start_at, dict(startAtMs=start_at, steps=0, recordedMinutes=0))
                bucket['steps'] += steps
                bucket['recordedMinutes'] += 1
                result['activityMinutes'].append([at*1000, steps])
            result['activity'] = list(hours.values())
            rows = db.execute("""SELECT observed,payload FROM observations WHERE device=? AND metric='stress'
                AND observed>=? AND observed<? ORDER BY observed,identity""", (device, lower*1000, upper*1000))
            result['stress'] = [[row['observed'], json.loads(row['payload'])['score']] for row in rows]
        return result

    def query(self, arguments, today=None, *, local_view=False):
        allowed = {'metrics', 'start_date', 'end_date', 'device_id', 'view', 'offset'}
        if not isinstance(arguments, dict) or set(arguments) - allowed:
            raise ValueError('Invalid health history arguments')
        metrics = metric_list(arguments.get('metrics'), HISTORY_METRICS)
        today = today or datetime.now(BEIJING).date()
        dates = [arguments.get(key) for key in ('start_date', 'end_date')]
        if any(item is not None and (type(item) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', item)) for item in dates):
            raise ValueError('Invalid health history date')
        end = date.fromisoformat(dates[1]) if dates[1] else today
        start = date.fromisoformat(dates[0]) if dates[0] else end - timedelta(days=6)
        days = (end-start).days + 1
        if not 1 <= days <= 31 or not date(2000, 1, 1) <= start <= end <= today:
            raise ValueError('Expected 1–31 past or current dates')
        view = arguments.get('view', 'summary')
        offset = arguments.get('offset', 0)
        if view not in ('summary', 'timeline') or type(offset) is not int or not 0 <= offset <= 10000:
            raise ValueError('Invalid health history view')
        if (view == 'timeline' and (days != 1 or len(metrics) != 1)) or (view == 'summary' and offset):
            raise ValueError('Timeline requires a single date and metric')
        device = arguments.get('device_id')
        if device is not None and (type(device) is not str or not DEVICE.fullmatch(device)):
            raise ValueError('Invalid device identity')
        enabled = set(HISTORY_METRICS if local_view else self.settings()['historyMetrics'])
        response = dict(ok=bool(enabled.intersection(metrics)), source='pc_archive', timezone='Asia/Shanghai', startDate=start.isoformat(),
                        endDate=end.isoformat(), results={metric: dict(status='no_data' if metric in enabled else
                        'disabled_by_settings') for metric in metrics})
        if not enabled.intersection(metrics) or not self.database.exists():
            return response
        with self.connection() as db:
            devices = [row['id'] for row in db.execute('SELECT id FROM devices ORDER BY seen DESC,id')]
            if device is None and devices:
                device = devices[0]
            response.update(deviceId=device, availableDevices=devices)
            if device not in devices:
                return response
            for metric in enabled.intersection(metrics):
                if metric == 'heart_rate' and not self._has_heart_rate(db):
                    continue
                response['results'][metric] = self._query_metric(db, device, metric, start, days, view, offset, local_view)
        return response

    def _query_metric(self, db, device, metric, start, days, view, offset, local_view):
        values = []
        for number in range(days):
            day = start + timedelta(days=number)
            lower = day_start(day)
            upper = lower + 86400
            label = day.isoformat()
            record = dict(date=label)
            if metric == 'historical_sleep':
                row = db.execute('SELECT payload FROM nights WHERE device=? AND day=?', (device, label)).fetchone()
                record['value'] = json.loads(row[0]) if row else None
                if row and view == 'summary':
                    record['value'].pop('segments', None)
                elif row and not local_view and 'segments' in record['value']:
                    segments = record['value']['segments']
                    record['value']['segments'] = segments[offset:offset+100]
                    record['nextSegmentOffset'] = offset+100 if len(segments)>offset+100 else None
            elif metric == 'heart_rate':
                count, low, high = db.execute('''SELECT COUNT(*),MIN(bpm),MAX(bpm) FROM heart_rate
                    WHERE device=? AND measured>=? AND measured<?''', (device, lower, upper)).fetchone()
                record.update(recordedSamples=count, minimumBpm=low, maximumBpm=high)
                if view == 'timeline':
                    rows = db.execute('''SELECT measured,bpm,quality FROM heart_rate WHERE device=? AND measured>=?
                        AND measured<? ORDER BY measured LIMIT 101 OFFSET ?''', (device, lower, upper, offset)).fetchall()
                    record['samples'] = [dict(atMs=row['measured']*1000, bpm=row['bpm'], nativeQuality=row['quality'])
                                         for row in rows[:100]]
                    record['nextSampleOffset'] = offset+100 if len(rows)>100 else None
            else:
                observations = db.execute('''SELECT observed,payload FROM observations
                    WHERE device=? AND metric=? AND observed>=? AND observed<? ORDER BY observed,identity''',
                    (device, metric, lower*1000, upper*1000)).fetchall()
                samples = [json.loads(row['payload']) for row in observations]
                if metric == 'activity':
                    if view == 'timeline':
                        rows = db.execute('''SELECT minute,steps FROM activity WHERE device=? AND minute>=? AND minute<?
                            ORDER BY minute''', (device, lower, upper)).fetchall()
                        hours = [dict(startAtMs=(lower + hour*3600)*1000, steps=None, recordedMinutes=0)
                                 for hour in range(24)]
                        for row in rows:
                            hour = hours[(row['minute'] - lower)//3600]
                            hour['steps'] = (hour['steps'] or 0) + row['steps']
                            hour['recordedMinutes'] += 1
                        count = len(rows)
                        total = sum(row['steps'] for row in rows) if count else None
                        record['hours'] = hours
                        record['minutes'] = [dict(atMs=row['minute']*1000, steps=row['steps'])
                                             for row in rows[offset:offset+100]]
                        record['nextMinuteOffset'] = offset+100 if count > offset+100 else None
                    else:
                        total, count = db.execute('''SELECT SUM(steps),COUNT(*) FROM activity
                            WHERE device=? AND minute>=? AND minute<?''', (device, lower, upper)).fetchone()
                    record.update(stepsFromRecords=total, recordedMinutes=count,
                                  latestSnapshot=samples[-1] if samples else None)
                elif metric == 'stress':
                    scores = [sample['score'] for sample in samples]
                    record.update(recordedSamples=len(scores), minimumScore=min(scores, default=None),
                                  maximumScore=max(scores, default=None), latestSnapshot=samples[-1] if samples else None)
                else:
                    counts = {}
                    for sample in samples:
                        value = sample['value']
                        counts[value] = counts.get(value, 0) + 1
                    times = [row['observed'] for row in observations]
                    record.update(observationCounts=counts,
                                  firstObservedAtMs=times[0] if times else None, lastObservedAtMs=times[-1] if times else None,
                                  maximumGapMs=max((b-a for a,b in zip(times, times[1:])), default=None))
                if view == 'timeline':
                    record['observations'] = samples[offset:offset+100]
                    record['nextObservationOffset'] = offset+100 if len(samples)>offset+100 else None
            if metric in ('activity', 'heart_rate', 'historical_sleep'):
                clause = 'end>=? AND end<?' if metric == 'historical_sleep' else 'end>=? AND start<?'
                coverage = db.execute(f'''SELECT start,end,status,checked FROM coverage
                    WHERE device=? AND metric=? AND {clause} ORDER BY start,end''',
                    (device, metric, lower, upper)).fetchall()
                record['lastCheckedAtMs'] = max((item['checked'] for item in coverage), default=None)
                if metric in ('activity', 'heart_rate'):
                    record['completeWindow'] = self.complete_through(coverage, lower) >= upper-60
                record['windowStatuses'] = sorted({item['status'] for item in coverage})
            values.append(record)
        available = [bool(row.get('value') or row.get('recordedMinutes') or row.get('recordedSamples') or row.get('latestSnapshot')
                          or row.get('observationCounts')) for row in values]
        full = all(available) and (metric not in ('activity', 'heart_rate') or all(row['completeWindow'] for row in values))
        status = ('no_data' if not any(available) else 'observations' if metric in ('wear','current_sleep','stress')
                  else 'complete' if full else 'partial')
        return dict(status=status, days=values)
