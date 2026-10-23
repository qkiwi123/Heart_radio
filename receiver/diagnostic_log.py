"""Bounded, explicitly managed PC diagnostics. Business history is never adopted or deleted."""
import gzip
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

DEFAULTS = dict(schemaVersion=1, maxFileMiB=10, maxTotalMiB=200, retentionDays=14,
                compressionAfterDays=2, keepAllDiagnostics=False,
                maintenanceIntervalSeconds=3600, maxMaintenanceFiles=2)
PART = re.compile(r'^part-(\d{8})-(\d+)\.jsonl(?:\.gz)?$')


def process_start(pid):
    """0 means gone, None means unknown. Never reclaim a stream on an access/identity error."""
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.GetProcessTimes.argtypes=[w.HANDLE]+[ctypes.POINTER(w.FILETIME)]*4
    kernel.CloseHandle.argtypes=[w.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return 0 if ctypes.get_last_error()==87 else None
    try:
        created,exited,system,user=(w.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle,ctypes.byref(created),ctypes.byref(exited),ctypes.byref(system),ctypes.byref(user)):return None
        return (created.dwHighDateTime<<32)|created.dwLowDateTime
    finally:kernel.CloseHandle(handle)


def project_root(path):
    for parent in Path(path).resolve().parents:
        if parent.name == 'runs': return parent.parent
    return None


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temp, path)
    finally: temp.unlink(missing_ok=True)


def policy(root, create=False):
    path = Path(root) / 'runs/logging-settings.json'
    try: value = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        value = dict(DEFAULTS)
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open('x', encoding='utf-8') as stream: json.dump(value, stream, ensure_ascii=False, indent=2)
            except FileExistsError: return policy(root)
    if not isinstance(value, dict) or set(value) != set(DEFAULTS) or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1:
        raise ValueError('Invalid diagnostic log policy; cleanup disabled')
    if type(value['keepAllDiagnostics']) is not bool: raise ValueError('Invalid diagnostic retention flag')
    for key, low, high in [('maxFileMiB',1,100),('maxTotalMiB',10,10000),('retentionDays',1,3650),
                           ('compressionAfterDays',1,3650),('maintenanceIntervalSeconds',60,86400),('maxMaintenanceFiles',1,10)]:
        if type(value[key]) is not int or not low <= value[key] <= high: raise ValueError('Invalid diagnostic limit: '+key)
    return value


class DiagnosticLog:
    """File-compatible append sink. New files only; legacy files are never enrolled implicitly."""
    def __init__(self, path, keep_open=True, max_bytes=None):
        self.path = Path(path).resolve(); self.name = str(self.path)
        self.root = project_root(self.path); self.keep_open = keep_open
        # Legacy experiments sometimes called their only history file pc.jsonl: never rotate it.
        if self.path.name not in ('bluetooth.jsonl','pc-bridge.jsonl') and not re.fullmatch(r'tools-[0-9a-f]{32}\.jsonl',self.path.name):
            self.root = None
        self.lock = threading.RLock(); self.stream = None; self.closed = False; self.error = None
        self.size = 0; self.part = 0; self.folder = None; self.metadata = None; self.failed = False
        try: settings = policy(self.root) if self.root else DEFAULTS
        except (OSError, ValueError) as error: settings = DEFAULTS; self.error = str(error)
        self.maximum = max_bytes if max_bytes is not None else settings['maxFileMiB'] * 1024**2
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.with_name(self.path.name+'.index.json').exists():
                raise FileExistsError('Retained diagnostic stream already uses this path')
            self.stream = self.path.open('xb')  # Refuse to overwrite/adopt any existing data.
        except FileExistsError:
            raise
        except OSError as error:
            self.error = '诊断日志创建失败，业务仍可继续：'+str(error); self.failed = True
            return
        if self.root:
            try:
                self.folder = self.root / 'runs/diagnostics' / uuid.uuid4().hex
                self.folder.mkdir(parents=True)
                self.metadata = dict(schemaVersion=1, source=self.path.relative_to(self.root).as_posix(),
                                     createdAtMs=time.time_ns()//1_000_000, closed=False, lastPart=0,
                                     ownerPid=os.getpid(),ownerStarted=process_start(os.getpid()))
                atomic_json(self.folder / 'stream.json', self.metadata)
                atomic_json(self.path.with_name(self.path.name+'.index.json'),
                            dict(schemaVersion=1, directory=self.folder.relative_to(self.root).as_posix()))
            except OSError as error:
                self.error = str(error); self.folder = None; self.metadata = None
        if not keep_open: self.stream.close(); self.stream = None

    def _roll(self):
        if self.stream: self.stream.close(); self.stream = None
        if self.folder is None: return  # Explicit external experiment paths keep their original behavior.
        self.part += 1
        destination = self.folder / f'part-{self.part:08d}-{time.time_ns()//1_000_000}.jsonl'
        os.replace(self.path, destination)
        self.stream = self.path.open('xb'); self.size = 0
        self.metadata['lastPart'] = self.part
        atomic_json(self.folder/'stream.json', self.metadata)

    def write(self, text):
        data = text.encode('utf-8')
        with self.lock:
            if self.closed: raise ValueError('Diagnostic log is closed')
            if self.failed: return 0
            try:
                if self.folder is not None and self.size and self.size + len(data) > self.maximum: self._roll()
                if self.stream is None: self.stream = self.path.open('ab')
                self.stream.write(data); self.stream.flush(); self.size += len(data)
                if not self.keep_open: self.stream.close(); self.stream = None
                return len(text)
            except OSError as error:
                self.error = '诊断日志写入失败，业务仍可继续：'+str(error); self.failed = True
                if self.stream:
                    try: self.stream.close()
                    except OSError: pass
                    self.stream = None
                return 0

    def flush(self):
        with self.lock:
            if self.stream:
                try: self.stream.flush()
                except OSError as error: self.error = str(error); self.failed = True

    def close(self):
        with self.lock:
            if self.closed: return
            if self.stream:
                try: self.stream.close()
                except OSError as error: self.error = str(error)
                self.stream = None
            self.closed = True
            if self.metadata:
                self.metadata.update(closed=True, closedAtMs=time.time_ns()//1_000_000)
                try: atomic_json(self.folder/'stream.json', self.metadata)
                except OSError as error: self.error = str(error)


def _retained_paths(path):
    path = Path(path).resolve(); root = project_root(path); folder = None
    if root:
        try:
            index = json.loads(path.with_name(path.name+'.index.json').read_text(encoding='utf-8'))
            candidate = (root/index['directory']).resolve()
            if candidate.parent == (root/'runs/diagnostics').resolve() and re.fullmatch('[0-9a-f]{32}',candidate.name): folder = candidate
        except (OSError,ValueError,KeyError,TypeError): pass
    if folder:
        parts = {}
        for item in folder.iterdir():
            match = PART.fullmatch(item.name)
            if match:
                key = match[1]
                if key not in parts or item.suffix == '.gz': parts[key] = item
        yield from (item for _, item in sorted(parts.items()))
    if path.exists(): yield path


def diagnostic_lines(path):
    """Read retained segments once, including compressed parts, then the active/last base file."""
    for item in _retained_paths(path):
        opener = gzip.open if item.suffix == '.gz' else open
        with opener(item, 'rt', encoding='utf-8') as stream: yield from stream


def diagnostic_tail(path, limit=20):
    """Read newest segments only, retaining at most limit lines for stop-receipt inspection."""
    result=[]
    for item in reversed(list(_retained_paths(path))):
        opener = gzip.open if item.suffix == '.gz' else open
        with opener(item, 'rt', encoding='utf-8') as stream:
            result=list(deque(stream,maxlen=limit-len(result)))+result
        if len(result)>=limit:break
    return result


def index_matches(source,folder,root):
    try:
        value=json.loads(source.with_name(source.name+'.index.json').read_text(encoding='utf-8'))
        return (Path(root)/value['directory']).resolve()==folder.resolve()
    except (OSError,ValueError,KeyError,TypeError):return False


def managed_files(root):
    """Only self-created stream manifests and closed parts inside this project's runs are eligible."""
    root = Path(root).resolve(); runs = root/'runs'; base = runs/'diagnostics'
    if not base.exists(): return []
    found = []
    for folder in base.iterdir():
        if (not folder.is_dir() or folder.is_symlink() or folder.resolve()!=folder.absolute()
                or not re.fullmatch('[0-9a-f]{32}',folder.name)): continue
        try:
            meta = json.loads((folder/'stream.json').read_text(encoding='utf-8'))
            original = root/meta['source']; source = original.resolve()
            if (meta.get('schemaVersion') != 1 or meta.get('category','diagnostic')!='diagnostic' or not index_matches(source,folder,root) or not source.is_relative_to(runs.resolve())
                    or source.name not in ('bluetooth.jsonl','pc-bridge.jsonl') and not re.fullmatch(r'tools-[0-9a-f]{32}\.jsonl',source.name)
                    or source != original.absolute()): continue
            common=dict(protected=meta.get('protectedUntilMs',0)/1000,legacy=meta.get('legacy') is True)
            for item in folder.iterdir():
                match = PART.fullmatch(item.name)
                if match and item.is_file() and not item.is_symlink():
                    found.append(dict(common,path=item, stamp=int(match[2])/1000, source=source, folder=folder, final=False))
            if meta.get('closed') is True and source.is_file():
                found.append(dict(common,path=source,stamp=meta['closedAtMs']/1000, source=source,folder=folder,final=True,part=meta.get('lastPart',0)+1))
        except (OSError,ValueError,KeyError,TypeError): continue
    return found


def export_diagnostic(source, destination):
    """Flatten retained segments for an explicitly requested evidence copy."""
    destination=Path(destination)
    with destination.open('w',encoding='utf-8') as output:
        for line in diagnostic_lines(source): output.write(line)


def _compress(row):
    source = row['path']
    initial=source.stat()
    destination = (row['folder']/f"part-{row['part']:08d}-{int(row['stamp']*1000)}.jsonl.gz"
                   if row['final'] else source.with_name(source.name+'.gz'))
    temporary = destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        original = hashlib.sha256()
        with source.open('rb') as incoming, gzip.open(temporary,'wb',compresslevel=1) as output:
            while True:
                chunk = incoming.read(65536)
                if not chunk: break
                original.update(chunk); output.write(chunk)
        verified = hashlib.sha256()
        with gzip.open(temporary,'rb') as incoming:
            while True:
                chunk = incoming.read(65536)
                if not chunk: break
                verified.update(chunk)
        if original.digest() != verified.digest(): raise OSError('Diagnostic archive verification failed')
        current=source.stat()
        if (initial.st_dev,initial.st_ino,initial.st_size,initial.st_mtime_ns)!=(current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns):
            raise OSError('Log changed during compression; original retained')
        os.replace(temporary,destination)
        source.unlink()
    finally: temporary.unlink(missing_ok=True)


def maintain(root, now=None, max_files=None):
    root = Path(root).resolve(); settings = policy(root); now = time.time() if now is None else now
    rows = managed_files(root)
    from log_retention import pinned
    mutable=lambda row:now>=row.get('protected',0) and not pinned(root,row['source'])
    total = sum(row['path'].stat().st_size for row in rows if mutable(row))
    actions = []; maximum = settings['maxTotalMiB']*1024**2; touched=set()
    for row in sorted(rows,key=lambda r:r['stamp']):
        if len(actions) >= (settings['maxMaintenanceFiles'] if max_files is None else max_files): break
        age = now-row['stamp']
        if age < 0: continue
        path = row['path']; size = path.stat().st_size
        if pinned(root,row['source']):continue
        # Bounded work, closed self-managed data only. Legacy files and business data never appear here.
        if mutable(row) and not settings['keepAllDiagnostics'] and (age >= settings['retentionDays']*86400 or total > maximum):
            path.unlink(); total -= size; actions.append(dict(action='delete',path=str(path))); touched.add(row['folder'])
        elif path.suffix != '.gz' and (row.get('legacy') or age >= settings['compressionAfterDays']*86400):
            _compress(row); actions.append(dict(action='compress',path=str(path)))
    for folder in touched:
        try:
            meta=json.loads((folder/'stream.json').read_text(encoding='utf-8'))
            source=(root/meta['source']).resolve()
            if meta.get('closed') is not True or source.exists() or set(p.name for p in folder.iterdir())!={'stream.json'}: continue
            index=source.with_name(source.name+'.index.json')
            if index.exists() and (root/json.loads(index.read_text(encoding='utf-8'))['directory']).resolve()==folder:
                index.unlink()
            (folder/'stream.json').unlink();folder.rmdir()
        except (OSError,ValueError,KeyError): pass
    remaining=managed_files(root)
    remaining_bytes=sum(row['path'].stat().st_size for row in remaining)
    budget_bytes=sum(row['path'].stat().st_size for row in remaining if mutable(row))
    more_work=any(not pinned(root,row['source']) and (
        (mutable(row) and not settings['keepAllDiagnostics'] and (budget_bytes>maximum or now-row['stamp']>=settings['retentionDays']*86400))
        or ((row.get('legacy') or now-row['stamp']>=settings['compressionAfterDays']*86400) and row['path'].suffix!='.gz')) for row in remaining)
    return dict(actions=actions,eligibleBytes=remaining_bytes,limitBytes=maximum,moreWork=bool(more_work),
                activeFilesExcluded=True,protectedBytes=remaining_bytes-budget_bytes,keepAllDiagnostics=settings['keepAllDiagnostics'])


class Maintenance:
    def __init__(self, root):
        self.root=Path(root); self.stop_event=threading.Event(); self.thread=None; self.error=None
    def start(self):
        from log_retention import settings as cleanup_settings,maintain_project,record_report
        try: policy(self.root,create=True);cleanup_settings(self.root,create=True)
        except (OSError,ValueError) as error:
            self.error=str(error)
            try:record_report(self.root,dict(actions=[],error=str(error),cleanupDisabled=True,protectedBusinessData=True))
            except OSError:pass
            return
        def run():
            # Never scan/compress logs on the startup or receiver/request thread.
            delay=60
            while not self.stop_event.wait(delay):
                try:
                    report=maintain_project(self.root)
                    record_report(self.root,report)
                    delay=5 if report['moreWork'] else policy(self.root)['maintenanceIntervalSeconds']; self.error=None
                except (OSError,ValueError) as error:
                    self.error=str(error);delay=3600
                    try:record_report(self.root,dict(actions=[],error=str(error),protectedBusinessData=True))
                    except OSError:pass
        self.thread=threading.Thread(target=run,name='diagnostic-maintenance',daemon=True); self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=3)
