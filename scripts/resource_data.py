"""Incremental receiver-log activity and PID-safe process grouping."""
import json
from pathlib import Path
import time
from resource_native import iso_filetime


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def receiver_path(root, metadata):
    path = Path(metadata['logPath']).resolve()
    if not path.is_relative_to(root / 'runs') or path.name != 'pc.jsonl':
        raise ValueError('接收日志不在本项目 runs 内')
    return path


class Activity:
    def __init__(self, root, event=lambda *a, **k: None):
        self.root, self.event = root, event
        self.path = None; self.offset = 0; self.pending = b''
        self.connected = False; self.packet_ns = self.valid_ns = None
        self.event_eligible = self.packet_eligible = False
        self.run_id = None
        self.reset_counts()

    def reset_counts(self):
        self.counts = dict(packets=0, sensorEvents=0, validEvents=0, connections=0, disconnects=0,
                           sequenceGaps=0, parseErrors=0, maxValidEventGapSeconds=0)

    def update(self, prime=False):
        metadata = read_json(self.root / 'runs' / 'active.json')
        path = receiver_path(self.root, metadata)
        if path != self.path:
            self.path = path; self.offset = 0; self.pending = b''
            self.connected = False; self.packet_ns = self.valid_ns = None
            self.event_eligible = self.packet_eligible = False
            self.run_id = metadata['runId']
            if not prime: self.event('receiver_run_changed', runId=self.run_id)
        size = path.stat().st_size
        if size < self.offset:
            self.offset = 0; self.pending = b''; self.connected = False
            self.packet_ns = self.valid_ns = None; self.event_eligible = False
            self.event('log_truncated', runId=self.run_id)
        with path.open('rb') as stream:
            if prime and size > 1024*1024:
                stream.seek(size - 1024*1024); stream.readline()
            else: stream.seek(self.offset)
            data = stream.read(); self.offset = stream.tell()
        lines = (self.pending+data).split(b'\n'); self.pending = lines.pop()
        for line in lines:
            if not line.strip(): continue
            try: self.consume(json.loads(line), prime)
            except (ValueError,KeyError,TypeError): self.counts['parseErrors'] += 1
        if prime: self.reset_counts()
        return self.snapshot()

    def consume(self, row, prime=False):
        kind = row.get('kind')
        if kind == 'wifi_connected':
            self.connected = True; self.event_eligible = False
            self.counts['connections'] += 1
        elif kind in ('wifi_disconnected','server_stop'):
            self.connected = False; self.event_eligible = False
            self.counts['disconnects'] += kind == 'wifi_disconnected'
        elif kind == 'packet':
            packet = row['packet']; ns = row['pcMonotonicNs']
            self.connected = True; self.packet_ns = ns
            self.counts['packets'] += 1
            self.packet_eligible = bool(packet.get('valid') and packet.get('running') and packet.get('registered'))
            if not self.packet_eligible: self.event_eligible = False
            if row.get('sequenceStep') not in (None,1): self.counts['sequenceGaps'] += 1
            if packet['kind'] == 'sensor_event':
                self.counts['sensorEvents'] += 1
                self.event_eligible = self.packet_eligible
                if self.event_eligible:
                    self.counts['validEvents'] += 1
                    if self.valid_ns is not None:
                        self.counts['maxValidEventGapSeconds'] = max(self.counts['maxValidEventGapSeconds'], (ns-self.valid_ns)/1e9)
                    self.valid_ns = ns
        if not prime and kind in ('wifi_connected','wifi_disconnected','server_stop','capture_window_complete','receiver_limit'):
            self.event('receiver_'+kind, runId=self.run_id, sourceReceiptMs=row.get('pcReceiptMs'))

    def snapshot(self):
        now = time.monotonic_ns()
        packet_age = (now-self.packet_ns)/1e9 if self.packet_ns else None
        valid_age = (now-self.valid_ns)/1e9 if self.valid_ns else None
        connected = self.connected and packet_age is not None and 0 <= packet_age <= 20
        return dict(self.counts, runId=self.run_id, logBytes=self.offset, packetAgeSeconds=packet_age,
                    validEventAgeSeconds=valid_age, connected=connected,
                    freshValid=bool(connected and self.event_eligible and valid_age is not None and 0 <= valid_age <= 30))


class Targets:
    def __init__(self, root, windows):
        self.root, self.windows = root, windows
        self.known = {}

    def sample(self):
        table = self.windows.processes(); selected = {}; issues = []
        root_alive = {}
        cache = {}
        def read(pid):
            if pid not in cache:
                cache[pid] = self.windows.process(pid, table.get(pid,{}).get('threads'))
            return cache[pid]
        queue = []
        for group, name in (('receiver','active.json'), ('dashboard','dashboard.json')):
            root_alive[group] = False
            try:
                meta = read_json(self.root/'runs'/name)
                row = read(int(meta['pid']))
                if row['creationTicks'] != iso_filetime(meta['processStartUtc']):
                    raise ValueError('进程编号已被其他进程复用')
                root_alive[group] = True; queue.append((row,group))
            except (OSError, ValueError, KeyError, TypeError) as error:
                issues.append(dict(group=group,error=str(error)))
        # Optional services may be stopped. Identity mismatches are never counted.
        bridge = self.root/'runs/capability-service.json'
        if bridge.exists():
            try:
                meta = read_json(bridge)
                row = read(int(meta['pid']))
                if (row['creationTicks'] == int(meta['creationTicks']) and
                        Path(row['executable']).resolve() == Path(meta['executable']).resolve()):
                    queue.append((row,'capability_bridge'))
            except OSError as error:
                if error.errno in (5,13) or getattr(error,'winerror',None) == 5:
                    issues.append(dict(group='capability_bridge',error=str(error)))
            except (ValueError, KeyError, TypeError) as error:
                issues.append(dict(group='capability_bridge',error=str(error)))
        for path in (self.root/'runs/mcp-adapters').glob('*.json'):
            try:
                meta = read_json(path)
                if (meta.get('schema') != 1 or meta.get('component') != 'mcp_adapter' or
                        Path(meta['project']).resolve() != self.root.resolve() or
                        path.name != f"{meta['pid']}-{meta['creationTicks']}.json"):
                    raise ValueError('MCP进程登记不属于本项目')
                row = read(int(meta['pid']))
                if (row['creationTicks'] == int(meta['creationTicks']) and
                        Path(row['executable']).resolve() == Path(meta['executable']).resolve()):
                    queue.append((row,'mcp_adapter'))
            except OSError as error:
                if error.errno in (5,13) or getattr(error,'winerror',None) == 5:
                    issues.append(dict(group='mcp_adapter',error=str(error)))
            except (ValueError, KeyError, TypeError) as error:
                issues.append(dict(group='mcp_adapter',error=str(error)))
        # Retain a previously verified child that outlives its launcher's process.
        for pid, known in self.known.items():
            try:
                row = read(pid)
                if row['creationTicks'] == known['creationTicks']: queue.append((row,known['group']))
            except OSError: pass
        while queue:
            row, group = queue.pop(0); pid = row['pid']
            if pid in selected: continue
            if Path(row['executable']).name.lower() == 'heartradiowidgets.exe': group = 'desktop_widgets'
            row = dict(row,group=group); selected[pid] = row
            for child in table.values():
                if child['parent'] != pid or child['pid'] in selected: continue
                try:
                    value = read(child['pid'])
                    if value['creationTicks'] >= row['creationTicks']: queue.append((value,group))
                except OSError as error:
                    issues.append(dict(group=group,pid=child['pid'],error=str(error)))
        self.known = selected
        return list(selected.values()), root_alive, issues
