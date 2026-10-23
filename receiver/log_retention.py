"""Explicitly scoped legacy/console cleanup. Never walks business or evidence trees."""
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from diagnostic_log import atomic_json, _compress, maintain, policy

DEFAULTS = dict(schemaVersion=1, adoptLegacyDiagnostics=True, manageConsole=True,
                legacyGraceDays=7, consoleRetentionDays=7, consoleMaxTotalMiB=50,
                consoleCompressionAfterDays=1)
RUN = re.compile(r'\d{8}-\d{6}-\d{3}')
BRIDGE = re.compile(r'\d{8}-\d{6}-[0-9a-f]{8}')


def settings(root, create=False):
    path=Path(root)/'runs/log-cleanup-settings.json'
    try:value=json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        value=dict(DEFAULTS)
        if create:
            path.parent.mkdir(parents=True,exist_ok=True)
            try:
                with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
            except FileExistsError:return settings(root)
    if not isinstance(value,dict) or set(value)!=set(DEFAULTS) or type(value['schemaVersion']) is not int or value['schemaVersion']!=1:
        raise ValueError('Invalid extended log cleanup settings; cleanup disabled')
    for key in ('adoptLegacyDiagnostics','manageConsole'):
        if type(value[key]) is not bool:raise ValueError('Invalid cleanup flag')
    for key,high in [('legacyGraceDays',365),('consoleRetentionDays',3650),('consoleMaxTotalMiB',10000),('consoleCompressionAfterDays',3650)]:
        if type(value[key]) is not int or not 1<=value[key]<=high:raise ValueError('Invalid cleanup limit: '+key)
    return value


def kind(root,path):
    root=Path(root).resolve();path=Path(path)
    if path.resolve()!=path.absolute() or not path.is_relative_to(root/'runs'):return None
    parts=path.relative_to(root/'runs').parts
    if len(parts)==2 and RUN.fullmatch(parts[0]):
        if parts[1]=='bluetooth.jsonl':return 'diagnostic'
        if parts[1] in ('receiver.stdout.txt','receiver.stderr.txt'):return 'console'
    if len(parts)==3 and parts[0]=='capability-service' and BRIDGE.fullmatch(parts[1]):
        if parts[2]=='pc-bridge.jsonl':return 'diagnostic'
        if parts[2] in ('stdout.txt','stderr.txt'):return 'console'
    if parts==('data-access','tools.jsonl'):return 'diagnostic'
    if len(parts)==1 and parts[0] in ('dashboard.stdout.txt','dashboard.stderr.txt','connection-wifi-launch.log','widgets-error.txt'):
        return 'console'
    return None


def pinned(root,path):
    path=Path(path);runs=Path(root).resolve()/'runs'
    return (runs/'.keep-logs').exists() or (path.parent/'.keep-logs').exists()


@contextmanager
def closed_file(path):
    """Windows denies this read handle while any writer/reader holds the file. Fail closed elsewhere."""
    if os.name!='nt':
        yield None;return
    import msvcrt
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE]
    kernel.CreateFileW.restype=w.HANDLE
    handle=kernel.CreateFileW(str(Path(path).resolve()),0x80000000,0,None,3,0x80,None)
    if handle in (None,ctypes.c_void_p(-1).value):
        yield None;return
    try:fd=msvcrt.open_osfhandle(int(handle),os.O_RDONLY)
    except BaseException:
        kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle(handle);raise
    with os.fdopen(fd,'rb') as stream:yield stream


def candidates(root,now,config):
    root=Path(root).resolve();runs=root/'runs';found=[]
    if not runs.exists():return []
    paths=[runs/name for name in ('dashboard.stdout.txt','dashboard.stderr.txt','connection-wifi-launch.log','widgets-error.txt')]
    paths.append(runs/'data-access/tools.jsonl')
    for folder in runs.iterdir():
        if not folder.is_dir() or folder.resolve()!=folder.absolute():continue
        if RUN.fullmatch(folder.name):paths.extend(folder/name for name in ('bluetooth.jsonl','receiver.stdout.txt','receiver.stderr.txt'))
    bridge=runs/'capability-service'
    if bridge.is_dir() and bridge.resolve()==bridge.absolute():
        for folder in bridge.iterdir():
            if folder.is_dir() and folder.resolve()==folder.absolute() and BRIDGE.fullmatch(folder.name):
                paths.extend(folder/name for name in ('pc-bridge.jsonl','stdout.txt','stderr.txt'))
    for path in paths:
        category=kind(root,path)
        if not category or not path.is_file() or path.with_name(path.name+'.index.json').exists() or pinned(root,path):continue
        if category=='diagnostic' and not config['adoptLegacyDiagnostics'] or category=='console' and not config['manageConsole']:continue
        stat=path.stat()
        if stat.st_size==0 or now-stat.st_mtime<86400 and stat.st_size<10*1024**2:continue
        found.append(dict(path=path,category=category,stamp=stat.st_mtime,bytes=stat.st_size))
    return sorted(found,key=lambda r:r['stamp'])


def adopt(root,row,now,config):
    source=row['path'];folder=Path(root).resolve()/'runs/diagnostics'/uuid.uuid4().hex
    # Keep the exclusion handle through both metadata publications. New writers use exclusive creation.
    with closed_file(source) as stream:
        if stream is None:return None
        stat=os.fstat(stream.fileno())
        if stat.st_mtime!=row['stamp'] or stat.st_size!=row['bytes'] or pinned(root,source):return None
        meta=dict(schemaVersion=1,source=source.relative_to(Path(root).resolve()).as_posix(),closed=True,lastPart=0,
                  createdAtMs=int(now*1000),closedAtMs=int(stat.st_mtime*1000),category=row['category'],legacy=True,
                  protectedUntilMs=int((now+config['legacyGraceDays']*86400)*1000))
        try:
            atomic_json(folder/'stream.json',meta)
            atomic_json(source.with_name(source.name+'.index.json'),dict(schemaVersion=1,directory=folder.relative_to(Path(root).resolve()).as_posix()))
        except BaseException:
            (folder/'stream.json').unlink(missing_ok=True)
            try:folder.rmdir()
            except OSError:pass
            raise
    return dict(action='adopt',path=str(source),category=row['category'],protectedUntilMs=meta['protectedUntilMs'])


def console_rows(root):
    root=Path(root).resolve();base=root/'runs/diagnostics';rows=[]
    if not base.is_dir():return []
    from diagnostic_log import PART,index_matches
    for folder in base.iterdir():
        if not re.fullmatch('[0-9a-f]{32}',folder.name) or folder.resolve()!=folder.absolute():continue
        try:
            meta=json.loads((folder/'stream.json').read_text(encoding='utf-8'));source=root/meta['source']
            if meta.get('category')!='console' or meta.get('closed') is not True or kind(root,source)!='console' or not index_matches(source,folder,root):continue
            common=dict(source=source,folder=folder,protected=meta.get('protectedUntilMs',0)/1000,legacy=True)
            part=meta.get('lastPart',0)
            for path in folder.iterdir():
                match=PART.fullmatch(path.name)
                if match and path.is_file() and path.resolve()==path.absolute():
                    part=max(part,int(match[1]));rows.append(dict(common,path=path,stamp=int(match[2])/1000,final=False))
            if source.is_file():rows.append(dict(common,path=source,stamp=source.stat().st_mtime,final=True,part=part+1))
        except (OSError,ValueError,KeyError,TypeError):continue
    return rows


def finish_orphan(root,now):
    from diagnostic_log import process_start,index_matches
    root=Path(root).resolve();base=root/'runs/diagnostics'
    if not base.is_dir():return None
    for folder in base.iterdir():
        if not re.fullmatch('[0-9a-f]{32}',folder.name) or folder.resolve()!=folder.absolute():continue
        try:
            meta=json.loads((folder/'stream.json').read_text(encoding='utf-8'));source=root/meta['source']
            if meta.get('category','diagnostic')!='diagnostic' or (source.name not in ('bluetooth.jsonl','pc-bridge.jsonl') and not re.fullmatch(r'tools-[0-9a-f]{32}\.jsonl',source.name)):continue
            if meta.get('closed') is not False or type(meta.get('ownerPid')) is not int or type(meta.get('ownerStarted')) is not int or meta['ownerStarted']<=0:continue
            if not index_matches(source,folder,root) or not source.resolve().is_relative_to(root/'runs') or pinned(root,source):continue
            live=process_start(meta['ownerPid'])
            if live is None or live==meta['ownerStarted']:continue
            stamp=now
            if source.exists():
                with closed_file(source) as stream:
                    if stream is None:continue
                    stamp=os.fstat(stream.fileno()).st_mtime
            meta.update(closed=True,closedAtMs=int(stamp*1000),recoveredOrphan=True)
            atomic_json(folder/'stream.json',meta)
            return dict(action='close_orphan',path=str(source))
        except (OSError,ValueError,KeyError,TypeError):continue
    return None


@contextmanager
def maintenance_lock(root):
    if os.name=='nt':
        from ctypes import wintypes as w
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateMutexW.argtypes=[w.LPVOID,w.BOOL,w.LPCWSTR];kernel.CreateMutexW.restype=w.HANDLE
        kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]
        kernel.ReleaseMutex.argtypes=[w.HANDLE];kernel.CloseHandle.argtypes=[w.HANDLE]
        token=hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:24]
        handle=kernel.CreateMutexW(None,False,'Local\\HeartRadioLogCleanup-'+token)
        if not handle:raise OSError('Cannot acquire log maintenance mutex')
        owned=False
        try:
            status=kernel.WaitForSingleObject(handle,0);owned=status in (0,128)
            yield owned
        finally:
            if owned:kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
    else:
        import fcntl
        path=Path(root)/'runs/.log-maintenance.lock';path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('a') as stream:
            try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:yield False
            else:
                try:yield True
                finally:fcntl.flock(stream,fcntl.LOCK_UN)


def maintain_project(root,now=None,dry_run=False):
    if dry_run:return _maintain_project(root,now,True)
    with maintenance_lock(root) as owned:
        if not owned:return dict(actions=[],moreWork=True,skipped='another maintenance run',protectedBusinessData=True)
        return _maintain_project(root,now,False)


def _maintain_project(root,now=None,dry_run=False):
    """At most the configured number of actions across adoption, diagnostics and consoles."""
    root=Path(root).resolve();now=time.time() if now is None else now
    config=settings(root);limits=policy(root);pending=candidates(root,now,config)
    if dry_run:
        return dict(dryRun=True,actions=[],candidateFiles=len(pending),candidateBytes=sum(r['bytes'] for r in pending),
                    candidates=[dict(path=str(r['path']),category=r['category'],bytes=r['bytes']) for r in pending],
                    policy=limits,extendedPolicy=config,protectedBusinessData=True)
    actions=[];maximum=limits['maxMaintenanceFiles']
    orphan=finish_orphan(root,now)
    if orphan:actions.append(orphan)
    else:
        for row in pending:
            result=adopt(root,row,now,config)
            if result:actions.append(result);break
    report=maintain(root,now,max_files=maximum-len(actions));actions.extend(report['actions'])
    rows=console_rows(root) if config['manageConsole'] else [];blocked=set()
    budget=sum(r['path'].stat().st_size for r in rows if now>=r['protected'] and not pinned(root,r['source']))
    for row in sorted(rows,key=lambda r:r['stamp']):
        if len(actions)>=maximum:break
        if pinned(root,row['source']):continue
        path=row['path'];size=path.stat().st_size;age=now-row['stamp']
        if not limits['keepAllDiagnostics'] and now>=row['protected'] and (age>=config['consoleRetentionDays']*86400 or budget>config['consoleMaxTotalMiB']*1024**2):
            with closed_file(path) as stream:
                if stream is None:blocked.add(path);continue
            path.unlink();budget-=size;actions.append(dict(action='delete',path=str(path),category='console'))
        elif path.suffix!='.gz' and (row['legacy'] or age>=config['consoleCompressionAfterDays']*86400):
            with closed_file(path) as stream:
                if stream is None:blocked.add(path);continue
            _compress(row);actions.append(dict(action='compress',path=str(path),category='console'))
    for folder in {r['folder'] for r in rows}:
        try:
            meta=json.loads((folder/'stream.json').read_text(encoding='utf-8'));source=root/meta['source']
            if source.exists() or set(p.name for p in folder.iterdir())!={'stream.json'}:continue
            index=source.with_name(source.name+'.index.json')
            if index.exists():index.unlink()
            (folder/'stream.json').unlink();folder.rmdir()
        except (OSError,ValueError,KeyError):pass
    remaining=console_rows(root)
    console_work=config['manageConsole'] and any(r['path'] not in blocked and not pinned(root,r['source']) and (r['path'].suffix!='.gz' or not limits['keepAllDiagnostics'] and now>=r['protected'] and
                    (now-r['stamp']>=config['consoleRetentionDays']*86400 or budget>config['consoleMaxTotalMiB']*1024**2)) for r in remaining)
    history_bytes=sum(p.stat().st_size for p in (root/'runs').glob('????????-??????-???/pc.jsonl') if p.is_file() and p.resolve()==p.absolute())
    return dict(actions=actions,moreWork=report['moreWork'] or console_work or bool(orphan) or bool(actions and len(pending)>1),
                diagnosticBytes=report['eligibleBytes'],consoleBytes=sum(r['path'].stat().st_size for r in remaining),
                pendingLegacyFiles=len(pending),protectedBusinessData=True,protectedHistoryBytes=history_bytes,
                historyAttentionNeeded=history_bytes>=1024**3,historyAutomaticDeletion=False,keepAllDiagnostics=limits['keepAllDiagnostics'])


def record_report(root,report):
    root=Path(root);stamp=time.time_ns()//1_000_000
    value=dict(report,completedAtMs=stamp);atomic_json(root/'runs/diagnostics-maintenance.json',value)
    if not report.get('actions') and not report.get('error'):return
    path=root/'runs/log-cleanup-history.json'
    try:
        old=json.loads(path.read_text(encoding='utf-8')) if path.stat().st_size<=1024**2 else []
        if not isinstance(old,list):old=[]
    except (OSError,ValueError):old=[]
    counts={}
    for action in report.get('actions',[]):counts[action['action']]=counts.get(action['action'],0)+1
    old.append(dict(completedAtMs=stamp,counts=counts,error=report.get('error'),examples=report.get('actions',[])[-5:]))
    atomic_json(path,old[-100:])
