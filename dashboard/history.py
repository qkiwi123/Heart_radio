"""Rebuildable, incremental history index of this project's runs/*/pc.jsonl only."""
from contextlib import contextmanager
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import threading

BEIJING = timezone(timedelta(hours=8))
RUN_ID = re.compile(r'^\d{8}-\d{6}-\d{3}$')
POINT_LIMIT = 6000


def event_day(milliseconds):
    if type(milliseconds) not in (int, float) or not math.isfinite(milliseconds):
        raise ValueError('Invalid event time')
    value = datetime.fromtimestamp(milliseconds / 1000, BEIJING)
    if not 1970 <= value.year <= 9998:
        raise ValueError('Unsupported event year')
    return value.date().isoformat()


def period(params):
    if set(params) - {'year', 'month', 'day', 'run'}:
        raise ValueError('Unsupported history parameter')
    try:
        year = int(params['year'])
        month = int(params['month']) if params.get('month') else None
        day = int(params['day']) if params.get('day') else None
        if not 1970 <= year <= 9998 or (day is not None and month is None):
            raise ValueError()
        start = date(year, month if month is not None else 1, day if day is not None else 1)
        if day is not None:
            end, level = start + timedelta(days=1), 'day'
        elif month is not None:
            end, level = date(year + (month == 12), 1 if month == 12 else month + 1, 1), 'month'
        else:
            end, level = date(year + 1, 1, 1), 'year'
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError('Invalid year, month or day') from None
    run = params.get('run') or None
    if run is not None and not RUN_ID.fullmatch(run):
        raise ValueError('Invalid run identifier')
    label = start.isoformat()[:{'year': 4, 'month': 7, 'day': 10}[level]]
    return start.isoformat(), end.isoformat(), level, label, run


class HistoryStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.runs = self.root / 'runs'
        self.runs.mkdir(exist_ok=True)
        self.database = self.runs / 'history.sqlite3'
        self.lock = threading.RLock()
        self.warnings = []
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sources (
                    run_id TEXT PRIMARY KEY, identity TEXT, offset INTEGER DEFAULT 0,
                    size INTEGER DEFAULT 0, mtime INTEGER DEFAULT 0, prefix_len INTEGER DEFAULT 0,
                    prefix_hash TEXT DEFAULT '', segment INTEGER DEFAULT 0, session TEXT,
                    last_time REAL, last_event_time REAL, parse_errors INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS run_days (
                    run_id TEXT REFERENCES sources(run_id) ON DELETE CASCADE,
                    day TEXT, first_ms REAL, last_ms REAL, PRIMARY KEY(run_id, day)
                );
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT REFERENCES sources(run_id) ON DELETE CASCADE, offset INTEGER,
                    day TEXT, time_ms REAL, measurement_ms REAL, bpm REAL, accuracy INTEGER,
                    valid INTEGER, session TEXT, sequence INTEGER, segment INTEGER,
                    PRIMARY KEY(run_id, offset)
                );
                CREATE INDEX IF NOT EXISTS events_by_date ON events(day, time_ms);
                CREATE INDEX IF NOT EXISTS run_days_by_date ON run_days(day);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
        finally:
            db.close()

    def refresh(self):
        """Index only complete appended lines; never change any source log."""
        with self.lock, self.connection() as db, db:
            self.warnings = []
            found = set()
            for folder in sorted(self.runs.iterdir()):
                if not RUN_ID.fullmatch(folder.name):
                    continue
                path = folder / 'pc.jsonl'
                if not path.is_file():
                    continue
                # Exclude junction/symlink aliases as well as any path outside runs.
                if path.resolve() != path.absolute():
                    self.warnings.append(f'{folder.name}：跳过链接路径')
                    continue
                found.add(folder.name)
                db.execute('SAVEPOINT source_index')
                try:
                    self._index(db, folder.name, path)
                except OSError:
                    db.execute('ROLLBACK TO source_index')
                    self.warnings.append(f'{folder.name}：日志暂不可读，可能显示上次索引')
                finally:
                    db.execute('RELEASE source_index')
            for source in db.execute('SELECT run_id FROM sources').fetchall():
                if source['run_id'] not in found:
                    db.execute('DELETE FROM sources WHERE run_id=?', (source['run_id'],))

    def _index(self, db, run, path):
        stat = path.stat()
        identity = f'{stat.st_dev}:{stat.st_ino}'
        saved = db.execute('SELECT * FROM sources WHERE run_id=?', (run,)).fetchone()
        with path.open('rb') as stream:
            reset = saved is None
            if saved:
                prefix = hashlib.sha256(stream.read(saved['prefix_len'])).hexdigest()
                reset = (identity != saved['identity'] or stat.st_size < saved['size']
                         or prefix != saved['prefix_hash']
                         or (stat.st_size == saved['size'] and stat.st_mtime_ns != saved['mtime']))
            if reset:
                db.execute('DELETE FROM sources WHERE run_id=?', (run,))
                db.execute('INSERT INTO sources(run_id,identity) VALUES(?,?)', (run, identity))
                saved = db.execute('SELECT * FROM sources WHERE run_id=?', (run,)).fetchone()
            elif stat.st_size == saved['size'] and stat.st_mtime_ns == saved['mtime']:
                return
            offset, segment, session = saved['offset'], saved['segment'], saved['session']
            last_time, last_event_time, errors = saved['last_time'], saved['last_event_time'], saved['parse_errors']
            stream.seek(offset)
            while True:
                line_offset = stream.tell()
                line = stream.readline()
                if not line or not line.endswith(b'\n'):
                    break
                offset = stream.tell()
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    timestamp = row['pcReceiptMs']
                    day = event_day(timestamp)
                    kind = row['kind']
                    if kind == 'packet' and not isinstance(row.get('packet'), dict):
                        raise ValueError('Invalid packet')
                except (ValueError, KeyError, TypeError, OverflowError, OSError):
                    errors += 1
                    segment += 1
                    continue
                db.execute('''INSERT INTO run_days VALUES(?,?,?,?) ON CONFLICT(run_id,day)
                    DO UPDATE SET first_ms=MIN(first_ms,excluded.first_ms),last_ms=MAX(last_ms,excluded.last_ms)''',
                           (run, day, timestamp, timestamp))
                if last_time is not None and (timestamp < last_time or timestamp - last_time > 30000):
                    segment += 1
                last_time = timestamp
                if kind in ('server_start', 'server_stop', 'wifi_connected', 'wifi_disconnected'):
                    segment += 1
                if kind != 'packet':
                    continue
                packet = row['packet']
                identity=packet.get('session')
                next_session=str(identity) if type(identity) is int or isinstance(identity,str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',identity) else ''
                if session != next_session or row.get('sequenceStep') not in (None, 1):
                    segment += 1
                session = next_session
                eligible = (packet.get('valid') is True and packet.get('running', True) is True
                            and packet.get('registered', True) is True)
                if not eligible:
                    segment += 1
                if packet.get('kind') != 'sensor_event':
                    continue
                if last_event_time is not None and (timestamp < last_event_time or timestamp - last_event_time > 30000):
                    segment += 1
                last_event_time = timestamp
                bpm = packet.get('bpm')
                if type(bpm) not in (int, float) or not math.isfinite(bpm):
                    bpm = None
                eligible = eligible and bpm is not None and bpm > 0
                if not eligible:
                    segment += 1
                measurement = packet.get('watchReceiptMs')
                if type(measurement) not in (int, float) or not math.isfinite(measurement):
                    measurement = None
                accuracy, sequence = packet.get('accuracy'), packet.get('sequence')
                accuracy = accuracy if type(accuracy) is int else None
                sequence = sequence if type(sequence) is int else None
                db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (run, line_offset, day, timestamp, measurement, bpm, accuracy,
                            int(eligible), session, sequence, segment))
            prefix_len = min(4096, stat.st_size)
            stream.seek(0)
            prefix_hash = hashlib.sha256(stream.read(prefix_len)).hexdigest()
            db.execute('''UPDATE sources SET identity=?,offset=?,size=?,mtime=?,prefix_len=?,prefix_hash=?,
                segment=?,session=?,last_time=?,last_event_time=?,parse_errors=? WHERE run_id=?''',
                       (identity, offset, stat.st_size, stat.st_mtime_ns, prefix_len, prefix_hash,
                        segment, session, last_time, last_event_time, errors, run))

    def _warnings(self, db):
        count = db.execute('SELECT COALESCE(SUM(parse_errors),0) FROM sources').fetchone()[0]
        return list(self.warnings) + ([f'有 {count} 行损坏或不完整结构的日志已跳过'] if count else [])

    def catalog(self):
        with self.lock:
            self.refresh()
            with self.connection() as db:
                days = [dict(row) for row in db.execute('''
                    SELECT d.day AS date,COUNT(DISTINCT d.run_id) AS runCount,
                           COALESCE(SUM(e.n),0) AS eventCount,COALESCE(SUM(e.v),0) AS validCount
                    FROM run_days d LEFT JOIN
                        (SELECT run_id,day,COUNT(*) n,SUM(valid) v FROM events GROUP BY run_id,day) e
                    ON e.run_id=d.run_id AND e.day=d.day GROUP BY d.day ORDER BY d.day DESC''')]
                return dict(days=days, timezone='北京时间 UTC+8', timeBasis='电脑接收时间',
                            warnings=self._warnings(db), sourceCount=db.execute('SELECT COUNT(*) FROM sources').fetchone()[0])

    @staticmethod
    def _where(start, end, run, alias=''):
        prefix = alias + '.' if alias else ''
        sql, args = f'{prefix}day>=? AND {prefix}day<?', [start, end]
        if run:
            sql += f' AND {prefix}run_id=?'
            args.append(run)
        return sql, args

    @staticmethod
    def _stats(db, where, args):
        return dict(db.execute(f'''SELECT COUNT(*) AS eventCount,COALESCE(SUM(valid),0) AS validCount,
            MIN(CASE WHEN valid=1 THEN bpm END) AS minimum,
            MAX(CASE WHEN valid=1 THEN bpm END) AS maximum,
            AVG(CASE WHEN valid=1 THEN bpm END) AS eventMean FROM events WHERE {where}''', args).fetchone())

    def query(self, params):
        start, end, level, label, run = period(params)
        with self.lock:
            self.refresh()
            with self.connection() as db:
                if run and not db.execute('SELECT 1 FROM sources WHERE run_id=?', (run,)).fetchone():
                    raise ValueError('Unknown run identifier')
                where, args = self._where(start, end, run)
                stats = self._stats(db, where, args)
                stats.update(dict(db.execute(f'''SELECT COUNT(DISTINCT day) AS dayCount,
                    COUNT(DISTINCT run_id) AS runCount FROM run_days WHERE {where}''', args).fetchone()))
                # All runs in this period remain selectable, even while one is selected.
                runs = [dict(row) for row in db.execute('''SELECT d.run_id AS runId,MIN(d.first_ms) AS firstMs,
                    MAX(d.last_ms) AS lastMs,COALESCE(SUM(e.n),0) AS eventCount,COALESCE(SUM(e.v),0) AS validCount,
                    MIN(e.low) AS minimum,MAX(e.high) AS maximum FROM run_days d LEFT JOIN
                    (SELECT run_id,day,COUNT(*) n,SUM(valid) v,
                        MIN(CASE WHEN valid=1 THEN bpm END) low,MAX(CASE WHEN valid=1 THEN bpm END) high
                     FROM events WHERE day>=? AND day<? GROUP BY run_id,day) e
                    ON e.run_id=d.run_id AND e.day=d.day WHERE d.day>=? AND d.day<?
                    GROUP BY d.run_id ORDER BY firstMs DESC''', (start, end, start, end))]
                group = 'substr(day,1,7)' if level == 'year' else 'day'
                buckets = [dict(row) for row in db.execute(f'''SELECT {group} AS label,
                    COUNT(*) AS eventCount,SUM(valid) AS validCount,
                    MIN(CASE WHEN valid=1 THEN bpm END) AS minimum,
                    MAX(CASE WHEN valid=1 THEN bpm END) AS maximum,
                    AVG(CASE WHEN valid=1 THEN bpm END) AS eventMean
                    FROM events WHERE {where} GROUP BY {group} ORDER BY label''', args)]
                points = []
                if level == 'day':
                    bucket_size = max(1, math.ceil(stats['validCount'] / (POINT_LIMIT // 4)))
                    batch = []
                    def emit():
                        if not batch:
                            return
                        chosen = {0, len(batch)-1, min(range(len(batch)), key=lambda i: batch[i]['bpm']),
                                  max(range(len(batch)), key=lambda i: batch[i]['bpm'])}
                        points.extend(batch[i] for i in sorted(chosen))
                        batch.clear()
                    for row in db.execute(f'''SELECT run_id,segment,time_ms,bpm,measurement_ms FROM events
                            WHERE {where} AND valid=1 ORDER BY time_ms,run_id,offset''', args):
                        batch.append(dict(timeMs=row['time_ms'], bpm=row['bpm'], measurementMs=row['measurement_ms'],
                                          segment=f'{row["run_id"]}:{row["segment"]}'))
                        if len(batch) >= bucket_size:
                            emit()
                    emit()
                return dict(period=label, level=level, selectedRun=run, statistics=stats, runs=runs,
                            buckets=buckets, points=points, displayedPoints=len(points),
                            downsampled=level == 'day' and len(points) < stats['validCount'],
                            warnings=self._warnings(db), timezone='北京时间 UTC+8', timeBasis='电脑接收时间')

    def csv_chunks(self, params):
        start, end, _, _, run = period(params)
        self.refresh()
        where, args = self._where(start, end, run)
        with self.connection() as db:
            if run and not db.execute('SELECT 1 FROM sources WHERE run_id=?', (run,)).fetchone():
                raise ValueError('Unknown run identifier')
            buffer = io.StringIO(newline='')
            writer = csv.writer(buffer)
            writer.writerow(['runId', 'date_UTCplus8', 'pcReceiptMs', 'watchReceiptMs_approximate',
                             'bpm', 'accuracy', 'valid', 'session', 'sequence'])
            yield ('\ufeff' + buffer.getvalue()).encode('utf-8')
            buffer.seek(0); buffer.truncate(0)
            for index, row in enumerate(db.execute(f'''SELECT run_id,day,time_ms,measurement_ms,bpm,accuracy,
                    valid,session,sequence FROM events WHERE {where} ORDER BY time_ms,run_id,offset''', args), 1):
                writer.writerow(tuple(row))
                if index % 1000 == 0:
                    yield buffer.getvalue().encode('utf-8')
                    buffer.seek(0); buffer.truncate(0)
            if buffer.tell():
                yield buffer.getvalue().encode('utf-8')
