"""Register only this MCP adapter for optional PC resource accounting; no health data."""
from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid


def current_identity():
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.argtypes, kernel.GetCurrentProcess.restype = [], w.HANDLE
    kernel.GetProcessTimes.argtypes = [w.HANDLE] + [c.POINTER(w.FILETIME)] * 4
    kernel.GetProcessTimes.restype = w.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = w.BOOL
    handle = kernel.GetCurrentProcess()
    created, exited, system, user = (w.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(handle, c.byref(created), c.byref(exited), c.byref(system), c.byref(user)):
        raise c.WinError(c.get_last_error())
    executable = c.create_unicode_buffer(32768)
    size = w.DWORD(len(executable))
    if not kernel.QueryFullProcessImageNameW(handle, 0, executable, c.byref(size)):
        raise c.WinError(c.get_last_error())
    return dict(pid=os.getpid(), creationTicks=str((created.dwHighDateTime << 32) | created.dwLowDateTime),
                executable=executable.value)


def project_for(entry):
    runtime = entry.with_name('runtime.json')
    if runtime.is_file():
        root = Path(json.loads(runtime.read_text(encoding='utf-8'))['sourceProject']).resolve()
    elif entry.name == 'mcp_server.py' and entry.parent.name == 'agent':
        root = entry.parent.parent
    else:
        return None
    if not (root / 'scripts/Start-HeartRadio.ps1').is_file():
        return None
    return root


def register(entry, identity=None):
    entry = Path(entry).resolve()
    root = project_for(entry)
    if root is None: return None
    value = dict(schema=1, component='mcp_adapter', project=str(root), entryPoint=str(entry),
                 entrySha256=hashlib.sha256(entry.read_bytes()).hexdigest(), **(identity or current_identity()))
    folder = root / 'runs/mcp-adapters'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{value['pid']}-{value['creationTicks']}.json"
    temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False) + '\n', encoding='utf-8')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target, value


def unregister(registration):
    if registration is None: return
    path, expected = registration
    try:
        # Never remove another instance's record, including a replaced file.
        if json.loads(path.read_text(encoding='utf-8')) == expected:
            path.unlink()
    except FileNotFoundError:
        pass


@contextmanager
def registered(entry):
    registration = None
    if os.name == 'nt':
        try: registration = register(entry)
        except (OSError, ValueError, KeyError, TypeError):
            print('HeartRadio: optional resource registration unavailable; MCP remains available.', file=sys.stderr)
    try:
        yield registration
    finally:
        try: unregister(registration)
        except (OSError, ValueError):
            print('HeartRadio: resource record cleanup unavailable; stale identities are ignored.', file=sys.stderr)
