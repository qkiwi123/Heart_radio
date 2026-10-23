"""Two-author causal permission register. No device wall clock or automatic renewal.

Only human controls create an enabling event. Network exchange merges existing events.
Each author retains its latest event and the peer version observed at that edit.
Concurrent events close first; a later explicit edit can open again.
"""
import copy
import ctypes
import json
import os
from pathlib import Path
import re
import time
import uuid
from control import atomic_json

AUTHORS = ('pc', 'watch')
MAX_MS = 31536000000
MAX_SEQ = 2147483647


def uptime_ms():
    if os.name == 'nt':
        call = ctypes.windll.kernel32.GetTickCount64
        call.restype = ctypes.c_ulonglong
        return call()
    return time.monotonic_ns() // 1000000


def boot_identity():
    if os.name == 'nt':
        # SYSTEM_BOOT_ENVIRONMENT_INFORMATION starts with the kernel boot GUID.
        data = ctypes.create_string_buffer(64)
        if ctypes.windll.ntdll.NtQuerySystemInformation(90, data, len(data), None) == 0:
            return data.raw[:16].hex()
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return uuid.uuid4().hex  # Unknown boot: conservatively close on restoration.


def validate_wire(value):
    if not isinstance(value, dict) or set(value) != {'pc', 'watch'}:
        raise ValueError('Invalid interaction snapshot')
    result = copy.deepcopy(value)
    for event in result.values():
        if event is None:
            continue
        if not isinstance(event, dict) or set(event) != {'seq', 'seen', 'id', 'enabled', 'ttlMs', 'remainingMs'}:
            raise ValueError('Invalid interaction event fields')
        for key, low in (('seq', 1), ('seen', 0)):
            if type(event[key]) is not int or not low <= event[key] <= MAX_SEQ:
                raise ValueError('Invalid interaction version')
        if type(event['enabled']) is not bool or not isinstance(event['id'], str) or not re.fullmatch('[0-9a-f]{32}', event['id']):
            raise ValueError('Invalid interaction identity')
        ttl, remaining = event['ttlMs'], event['remainingMs']
        if ttl is None:
            if remaining is not None:
                raise ValueError('Invalid unlimited lease')
        elif not event['enabled'] or type(ttl) is not int or not 1 <= ttl <= MAX_MS or type(remaining) is not int or not 0 <= remaining <= ttl:
            raise ValueError('Invalid interaction lease')
    pc, watch = result['pc'], result['watch']
    if (pc and pc['seen'] > (watch or {}).get('seq', 0)) or (watch and watch['seen'] > (pc or {}).get('seq', 0)):
        raise ValueError('Missing causal history')
    if pc and watch and pc['seen'] >= watch['seq'] and watch['seen'] >= pc['seq']:
        raise ValueError('Causal cycle')
    return result


class InteractionRegister:
    def __init__(self, author, now=uptime_ms):
        if author not in AUTHORS:
            raise ValueError('Invalid author')
        self.author, self.now = author, now
        self.events = dict.fromkeys(AUTHORS)
        self.deadlines = {}

    def active(self):
        pc, watch = self.events.values()
        if pc is None or watch is None:
            return [x for x in (pc, watch) if x is not None]
        if pc['seen'] >= watch['seq']:
            return [pc]
        if watch['seen'] >= pc['seq']:
            return [watch]
        return [pc, watch]

    @property
    def enabled(self):
        events = self.active()
        return bool(events) and all(x['enabled'] for x in events)

    def remaining(self):
        if not self.enabled:
            return None
        ends = [self.deadlines[x['id']] for x in self.active() if x['ttlMs'] is not None]
        return max(0, min(ends) - self.now()) if ends else None

    def fingerprint(self):
        return ':'.join(f"{(self.events[x] or {}).get('seq', 0)}-{(self.events[x] or {}).get('id', '')}" for x in AUTHORS)

    def edit(self, enabled, ttl_ms=None, force=False):
        if type(enabled) is not bool or (ttl_ms is not None and (type(ttl_ms) is not int or not 1 <= ttl_ms <= MAX_MS)):
            raise ValueError('Invalid interaction edit')
        if enabled and self.enabled and not force:
            return False
        peer = AUTHORS[1 - AUTHORS.index(self.author)]
        seq = (self.events[self.author] or {}).get('seq', 0) + 1
        if seq > MAX_SEQ:
            raise ValueError('Interaction version exhausted')
        event = dict(seq=seq, seen=(self.events[peer] or {}).get('seq', 0), id=uuid.uuid4().hex,
                     enabled=enabled, ttlMs=ttl_ms if enabled else None)
        self.events[self.author] = event
        if event['ttlMs'] is not None:
            self.deadlines[event['id']] = self.now() + ttl_ms
        self._prune()
        return True

    def expire(self):
        if self.enabled and self.remaining() == 0:
            self.edit(False)
            return True
        return False

    def wire(self):
        result = copy.deepcopy(self.events)
        for event in result.values():
            if event:
                event['remainingMs'] = max(0, self.deadlines[event['id']] - self.now()) if event['ttlMs'] is not None else None
        return result

    def merge(self, value):
        value = validate_wire(value)
        candidate = copy.deepcopy(self.events)
        for author, event in value.items():
            old = candidate[author]
            if event and (old is None or event['seq'] > old['seq']):
                candidate[author] = {k: v for k, v in event.items() if k != 'remainingMs'}
            elif event and old and event['seq'] == old['seq'] and {k: v for k, v in event.items() if k != 'remainingMs'} != old:
                raise ValueError('Conflicting event identity')
        # Validate the merged causal graph before changing any state.
        validate_wire({a: dict(e, remainingMs=e['ttlMs']) if e else None for a, e in candidate.items()})
        self.events = candidate
        for author, event in value.items():
            if event and event['ttlMs'] is not None and self.events[author]['id'] == event['id']:
                end = self.now() + event['remainingMs']
                self.deadlines[event['id']] = min(self.deadlines.get(event['id'], end), end)
        self._prune()
        self.expire()

    def _prune(self):
        ids = {e['id'] for e in self.events.values() if e}
        self.deadlines = {k: v for k, v in self.deadlines.items() if k in ids}


class InteractionStore:
    """Atomic state + local boot/monotonic deadlines. A failed save never ACKs."""
    def __init__(self, path, author='pc', now=uptime_ms, boot=None, save=atomic_json, label='交互'):
        self.path, self.save = Path(path), save
        self.label=label
        self.boot = boot or boot_identity()
        self.register = InteractionRegister(author, now)
        self.error = None
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                self.register.merge(value['state'])
                deadlines=value['deadlines']
                ids={e['id'] for e in self.register.events.values() if e and e['ttlMs'] is not None}
                if not isinstance(deadlines,dict) or any(k not in deadlines or type(deadlines[k]) is not int or deadlines[k]<0 for k in ids):
                    raise ValueError('Invalid saved deadlines')
                self.register.deadlines = {k:deadlines[k] for k in ids}
                if value['boot'] != self.boot:
                    self.register.edit(False)
                self.register.expire()
                self.save(self.path,dict(boot=self.boot,state=self.register.wire(),deadlines=self.register.deadlines))
            except (OSError, ValueError, KeyError, TypeError):
                self.register = InteractionRegister(author, now)
                self.error = f'{self.label}状态无法恢复，请明确关闭后重试'

    def transact(self, operation):
        candidate = copy.deepcopy(self.register)
        result = operation(candidate)
        value = dict(boot=self.boot, state=candidate.wire(), deadlines=candidate.deadlines)
        if candidate.events != self.register.events or candidate.deadlines != self.register.deadlines or self.error or not self.path.exists():
            try:
                self.save(self.path, value)
            except (OSError, ValueError):
                self.error = f'{self.label}设置保存失败，设备操作已暂停'
                raise
        self.register, self.error = candidate, None
        return result

    @property
    def enabled(self):
        return self.error is None and self.register.enabled
