"""Read-only presentation of receiver logs. Heartbeats never create measurements."""
from collections import deque
import json
from pathlib import Path
import time


def age_seconds(row, now_ns, now_ms):
    if row is None:
        return None
    monotonic = (now_ns - row['pcMonotonicNs']) / 1e9
    # A historical log after a PC reboot may use a different monotonic epoch.
    return max(0, (now_ms - row['pcReceiptMs']) / 1000 if monotonic < 0 else monotonic)


class State:
    def __init__(self):
        self.connected = False
        self.stopped = False
        self.server_start = None
        self.server_stop = None
        self.receiver_limit = None
        self.host = self.watch_address = None
        self.last_packet = None
        self.last_event = None
        self.current_allowed = False
        self.invalid_reason = '等待手表连接'
        self.packet_count = self.valid_count = self.event_count = 0
        self.connections = self.disconnects = self.sequence_gaps = 0
        self.session = None
        self.segment = 0
        self.points = deque(maxlen=7200)
        self.minimum = self.maximum = None
        self.notices = deque(maxlen=6)

    def consume(self, row):
        kind = row.get('kind')
        if kind == 'server_start':
            self.server_start = row
            self.host = row.get('host')
        elif kind == 'receiver_limit':
            self.receiver_limit = {key: row.get(key) for key in ('seconds','revision','endsMs')}
        elif kind == 'listener_changed':
            self.host = row.get('host')
        elif kind == 'wifi_connected':
            self.watch_address = row.get('target')
            self.host = row.get('host', self.host)
            self.connected = True; self.stopped = False; self.connections += 1
            self.current_allowed = False; self.last_event = None; self.segment += 1
            self.invalid_reason = '已连接，等待新的心率事件'
        elif kind in ('wifi_disconnected', 'server_stop'):
            self.connected = False; self.current_allowed = False; self.segment += 1
            if kind == 'server_stop': self.stopped = True; self.server_stop = row
            else: self.disconnects += 1
            self.notices.append(dict(timeMs=row['pcReceiptMs'], text='接收已结束' if self.stopped else '手表连接已断开'))
        elif kind == 'packet':
            p = row['packet']; self.last_packet = row; self.packet_count += 1
            if self.session != p['session']:
                self.session = p['session']; self.last_event = None; self.current_allowed = False; self.segment += 1
            if row.get('sequenceStep') not in (None,1):
                self.sequence_gaps += 1; self.segment += 1
            eligible = p['valid'] and p.get('running', True) and p.get('registered', True)
            if not eligible:
                if self.current_allowed: self.segment += 1
                self.current_allowed = False
                self.invalid_reason = '无接触，请检查佩戴' if p['accuracy'] == -1 else '暂无有效读数'
                if not p.get('running', True) or not p.get('registered', True):
                    self.invalid_reason = '手表当前未在采集'
            if p['kind'] == 'sensor_event':
                self.event_count += 1; self.last_event = row
                self.current_allowed = eligible
                if eligible:
                    self.valid_count += 1
                    self.minimum = p['bpm'] if self.minimum is None else min(self.minimum, p['bpm'])
                    self.maximum = p['bpm'] if self.maximum is None else max(self.maximum, p['bpm'])
                    self.points.append(dict(timeMs=row['pcReceiptMs'], measurementMs=p['watchReceiptMs'],
                                            bpm=p['bpm'], accuracy=p['accuracy'], segment=self.segment))

    def snapshot(self, now_ns=None, now_ms=None):
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        now_ms = time.time_ns()//1_000_000 if now_ms is None else now_ms
        packet_age = age_seconds(self.last_packet,now_ns,now_ms)
        event_age = age_seconds(self.last_event,now_ns,now_ms)
        link = self.connected and packet_age is not None and packet_age <= 20
        valid = link and self.current_allowed and event_age is not None and event_age <= 30
        if self.stopped: status = '电脑接收已停止'
        elif not self.connected: status = '等待手表连接' if self.connections == 0 else '连接已断开'
        elif packet_age is None: status = '已连接，等待数据'
        elif not link: status = '暂未收到手表数据'
        elif event_age is not None and event_age > 30: status = '超过30秒无新心率'
        elif not valid: status = self.invalid_reason
        else: status = '正在接收心率'
        p = self.last_event['packet'] if self.last_event else None
        latest = self.last_packet['packet'] if self.last_packet else None
        return dict(nowMs=now_ms, status=status, valid=valid, bpm=p['bpm'] if valid else None,
                    connected=link, receiverStopped=self.stopped, packetAgeSeconds=packet_age,
                    eventAgeSeconds=event_age, measurementMs=p['watchReceiptMs'] if p else None,
                    eventReceivedMs=self.last_event['pcReceiptMs'] if self.last_event else None,
                    eventSession=p.get('session') if p else None,
                    accuracy=latest['accuracy'] if latest else None, packetCount=self.packet_count,
                    sensorEventCount=self.event_count, validCount=self.valid_count,
                    connections=self.connections, disconnects=self.disconnects, sequenceGaps=self.sequence_gaps,
                    minimum=self.minimum, maximum=self.maximum,
                    startedMs=self.server_start['pcReceiptMs'] if self.server_start else None,
                    endedMs=self.server_stop['pcReceiptMs'] if self.server_stop else None,
                    receiverLimit=self.receiver_limit,
                    points=list(self.points), notices=list(self.notices))


class LogReader:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.run = None; self.path = None; self.offset = 0; self.pending = b''
        self.state = State(); self.parse_errors = 0

    def refresh(self):
        active = self.root / 'runs' / 'active.json'
        if not active.exists(): return self.state.snapshot()
        metadata = json.loads(active.read_text(encoding='utf-8-sig'))
        path = Path(metadata['logPath']).resolve()
        if not path.is_relative_to(self.root / 'runs') or path.name != 'pc.jsonl':
            raise ValueError('日志必须在本项目runs目录内')
        if path != self.path:
            self.path = path; self.offset = 0; self.pending = b''; self.state = State(); self.parse_errors = 0
        self.run = metadata
        if path.exists():
            with path.open('rb') as stream:
                stream.seek(self.offset); data = stream.read(); self.offset = stream.tell()
            lines = (self.pending+data).split(b'\n'); self.pending = lines.pop()
            for line in lines:
                if not line.strip(): continue
                try: self.state.consume(json.loads(line))
                except (ValueError,KeyError,TypeError): self.parse_errors += 1
        result = self.state.snapshot()
        result.update(runId=metadata['runId'], host=self.state.host, watchAddress=self.state.watch_address or metadata['watchAddress'],
                      durationSeconds=metadata['durationSeconds'], parseErrors=self.parse_errors)
        observation = path.parent/'human-observation.json'
        if observation.exists():
            result['observation'] = json.loads(observation.read_text(encoding='utf-8-sig'))
        basis = result.get('observation')
        start = dict(pcReceiptMs=basis['startedPcMs'], pcMonotonicNs=basis['startedPcMonotonicNs']) if basis else self.state.server_start
        end = self.state.server_stop
        result['elapsedSeconds'] = age_seconds(start, end['pcMonotonicNs'] if end else time.monotonic_ns(),
                                              end['pcReceiptMs'] if end else time.time_ns()//1_000_000) or 0
        return result
