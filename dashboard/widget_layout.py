"""The dashboard is the only writer of widget layout; patches never carry lock state."""
import copy
import json
import math
from pathlib import Path
import threading
import uuid

KINDS = ('current', 'trend')
FIELDS = {'left', 'top', 'width', 'height', 'topmost'}


class LayoutStore:
    def __init__(self, root):
        self.path = Path(root) / 'runs/widget-layout.json'
        self.backup = self.path.with_suffix('.last-good.json')
        self.lock = threading.RLock()
        self.clients = {}
        self.error = None

    def read(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8-sig'))
            if (not isinstance(value, dict) or any(not isinstance(value.get(k, {}), dict) for k in KINDS)
                    or type(value.get('_revision',0)) is not int or value.get('_revision',0)<0):
                raise ValueError('Invalid layout')
            self.error = None
            return value
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            self.error = '小窗布局暂不可读，原文件已保留'
            # A recoverable snapshot is read-only until an explicit valid edit is saved.
            try:
                value = json.loads(self.backup.read_text(encoding='utf-8-sig'))
                if (isinstance(value, dict) and all(isinstance(value.get(k, {}), dict) for k in KINDS)
                        and type(value.get('_revision',0)) is int and value.get('_revision',0)>=0):
                    return value
            except (OSError, ValueError): pass
            raise OSError(self.error) from error

    @staticmethod
    def atomic(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')), encoding='utf-8')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def save(self, before, value):
        # Publish backup first. A failed primary save never reports success or advances revision.
        if before: self.atomic(self.backup, before)
        self.atomic(self.path, value)
        self.error = None

    def snapshot(self):
        with self.lock:
            value = copy.deepcopy(self.read())
            return dict(layout=value, revision=value.get('_revision', 0), storageError=self.error)

    def locked(self):
        with self.lock:
            value = self.read()
            return {k: value.get(k, {}).get('locked', value.get(k, {}).get('movementLocked', False)) is True for k in KINDS}

    def set_locked(self, kind, enabled):
        if kind not in KINDS or type(enabled) is not bool: raise ValueError('无效的小窗固定参数')
        with self.lock:
            before = self.read(); value = copy.deepcopy(before)
            value.setdefault(kind, {})['locked'] = enabled
            value['_revision'] = before.get('_revision', 0) + 1
            self.save(before, value)
            return self.snapshot()

    def patch(self, kind, changes, client_id, sequence):
        if (kind not in KINDS or not isinstance(changes, dict) or not changes or set(changes) - FIELDS
                or not isinstance(client_id, str) or not 1 <= len(client_id) <= 64
                or any(c not in '0123456789abcdef' for c in client_id)
                or type(sequence) is not int or not 1 <= sequence <= 2**53-1):
            raise ValueError('无效的小窗布局请求')
        for key, number in changes.items():
            if key == 'topmost':
                if type(number) is not bool: raise ValueError('置顶状态必须是布尔值')
            elif type(number) not in (int, float) or not math.isfinite(number) or not -100000 <= number <= 100000:
                raise ValueError('小窗位置或大小无效')
        for name, low in (('width', 300 if kind == 'current' else 380), ('height', 198 if kind == 'current' else 220)):
            if name in changes and not low <= changes[name] <= 100000: raise ValueError('小窗大小超出范围')
        with self.lock:
            key = (client_id, kind)
            if sequence <= self.clients.get(key, 0): return dict(self.snapshot(), status='already_applied', sequence=sequence)
            before = self.read()
            locked = before.get(kind, {}).get('locked', before.get(kind, {}).get('movementLocked', False)) is True
            if locked:
                # A drag finishing after the user locks the window cannot change that locked snapshot.
                if len(self.clients) >= 128 and key not in self.clients: self.clients.pop(next(iter(self.clients)))
                self.clients[key] = sequence
                return dict(self.snapshot(), status='locked', sequence=sequence)
            value = copy.deepcopy(before)
            value.setdefault(kind, {}).update(changes)
            value['_revision'] = before.get('_revision', 0) + 1
            self.save(before, value)
            if len(self.clients) >= 128 and key not in self.clients: self.clients.pop(next(iter(self.clients)))
            self.clients[key] = sequence
            return dict(self.snapshot(), status='saved', sequence=sequence)
