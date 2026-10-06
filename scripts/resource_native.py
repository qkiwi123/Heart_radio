"""Read-only Windows process counters; no external packages or performance services."""
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import os
import re


class ProcessEntry(c.Structure):
    _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD),
                ('heap', c.c_size_t), ('module', w.DWORD), ('threads', w.DWORD),
                ('parent', w.DWORD), ('priority', w.LONG), ('flags', w.DWORD),
                ('name', w.WCHAR * 260)]


class Memory(c.Structure):
    _fields_ = [('size', w.DWORD), ('faults', w.DWORD)] + [(name, c.c_size_t) for name in
        ('peakWorkingSet', 'workingSet', 'peakPaged', 'paged', 'peakNonpaged', 'nonpaged',
         'pagefile', 'peakPagefile', 'private')]


class SystemMemory(c.Structure):
    _fields_ = [('length', w.DWORD), ('load', w.DWORD)] + [(name, c.c_ulonglong) for name in
        ('totalPhysical', 'availablePhysical', 'totalPage', 'availablePage', 'totalVirtual', 'availableVirtual', 'extended')]


def iso_filetime(value):
    """Preserve the seventh fractional digit in .NET process creation timestamps."""
    value = value.replace('Z', '+00:00')
    instant = datetime.fromisoformat(value)
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    delta = instant.astimezone(timezone.utc) - epoch
    fraction = re.search(r'\.(\d+)', value)
    seventh = int((fraction[1] + '0000000')[6]) if fraction else 0
    return (delta.days * 86400 + delta.seconds) * 10_000_000 + delta.microseconds * 10 + seventh


class Windows:
    def __init__(self):
        if os.name != 'nt':
            raise OSError('资源采样目前仅支持 Windows')
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.p = c.WinDLL('psapi', use_last_error=True)
        self.u = c.WinDLL('user32', use_last_error=True)
        specs = [
            (self.k.OpenProcess, [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            (self.k.CloseHandle, [w.HANDLE], w.BOOL),
            (self.k.GetProcessTimes, [w.HANDLE]+[c.POINTER(w.FILETIME)]*4, w.BOOL),
            (self.k.GetProcessHandleCount, [w.HANDLE,c.POINTER(w.DWORD)],w.BOOL),
            (self.k.QueryFullProcessImageNameW,[w.HANDLE,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD)],w.BOOL),
            (self.k.CreateToolhelp32Snapshot,[w.DWORD,w.DWORD],w.HANDLE),
            (self.k.Process32FirstW,[w.HANDLE,c.POINTER(ProcessEntry)],w.BOOL),
            (self.k.Process32NextW,[w.HANDLE,c.POINTER(ProcessEntry)],w.BOOL),
            (self.p.GetProcessMemoryInfo,[w.HANDLE,c.POINTER(Memory),w.DWORD],w.BOOL),
            (self.k.GlobalMemoryStatusEx,[c.POINTER(SystemMemory)],w.BOOL),
            (self.k.CreateMutexW,[c.c_void_p,w.BOOL,w.LPCWSTR],w.HANDLE),
            (self.k.WaitForSingleObject,[w.HANDLE,w.DWORD],w.DWORD),
            (self.k.ReleaseMutex,[w.HANDLE],w.BOOL),
        ]
        for function, args, result in specs:
            function.argtypes, function.restype = args, result

    @staticmethod
    def ticks(value):
        return (value.dwHighDateTime << 32) | value.dwLowDateTime

    def process(self, pid, threads=None):
        handle = self.k.OpenProcess(0x1000 | 0x10, False, pid)
        if not handle:
            raise OSError(c.get_last_error(), f'无法读取进程 {pid}')
        try:
            created, exited, kernel, user = (w.FILETIME() for _ in range(4))
            if not self.k.GetProcessTimes(handle, c.byref(created), c.byref(exited), c.byref(kernel), c.byref(user)):
                raise c.WinError(c.get_last_error())
            if self.ticks(exited):
                raise OSError(f'进程 {pid} 已退出')
            memory = Memory(); memory.size = c.sizeof(memory)
            if not self.p.GetProcessMemoryInfo(handle, c.byref(memory), memory.size):
                raise c.WinError(c.get_last_error())
            handles = w.DWORD()
            if not self.k.GetProcessHandleCount(handle, c.byref(handles)):
                raise c.WinError(c.get_last_error())
            path = c.create_unicode_buffer(32768); size = w.DWORD(len(path))
            if not self.k.QueryFullProcessImageNameW(handle, 0, path, c.byref(size)):
                raise c.WinError(c.get_last_error())
            return dict(pid=pid, creationTicks=self.ticks(created), executable=path.value,
                        cpuSeconds=(self.ticks(kernel)+self.ticks(user))/10_000_000,
                        workingSetBytes=memory.workingSet, privateCommitBytes=memory.private,
                        handles=handles.value, threads=threads)
        finally:
            self.k.CloseHandle(handle)

    def processes(self):
        handle = self.k.CreateToolhelp32Snapshot(2, 0)
        if handle == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            item = ProcessEntry(); item.size = c.sizeof(item)
            found = {}
            ok = self.k.Process32FirstW(handle, c.byref(item))
            while ok:
                found[item.pid] = dict(pid=item.pid, parent=item.parent, threads=item.threads, name=item.name)
                ok = self.k.Process32NextW(handle, c.byref(item))
            return found
        finally:
            self.k.CloseHandle(handle)

    def system(self):
        memory = SystemMemory(); memory.length = c.sizeof(memory)
        if not self.k.GlobalMemoryStatusEx(c.byref(memory)):
            raise c.WinError(c.get_last_error())
        return dict(logicalProcessors=os.cpu_count(), physicalMemoryBytes=memory.totalPhysical)

    def widget_visibility(self, pids):
        result = {'current': False, 'trend': False}
        callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self.u.EnumWindows.argtypes = [callback_type, w.LPARAM]
        self.u.GetWindowThreadProcessId.argtypes = [w.HWND,c.POINTER(w.DWORD)]
        self.u.IsWindowVisible.argtypes = [w.HWND]
        self.u.GetWindowTextW.argtypes = [w.HWND,w.LPWSTR,c.c_int]
        @callback_type
        def visit(handle, _):
            pid = w.DWORD(); self.u.GetWindowThreadProcessId(handle,c.byref(pid))
            if pid.value in pids and self.u.IsWindowVisible(handle):
                title = c.create_unicode_buffer(256); self.u.GetWindowTextW(handle,title,len(title))
                if title.value == 'HeartRadio · 桌面心率': result['current'] = True
                if title.value == 'HeartRadio · 桌面趋势': result['trend'] = True
            return True
        self.u.EnumWindows(visit, 0)
        return result

    def mutex(self, name):
        return Mutex(self, name)


class Mutex:
    def __init__(self, windows, name):
        self.k = windows.k
        self.handle = self.k.CreateMutexW(None, False, name)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        if self.k.WaitForSingleObject(self.handle, 0) not in (0, 0x80):
            self.k.CloseHandle(self.handle)
            raise RuntimeError('已有资源测试或启动操作在运行，请先查看进度')

    def __enter__(self): return self

    def __exit__(self, *args):
        self.k.ReleaseMutex(self.handle); self.k.CloseHandle(self.handle)
