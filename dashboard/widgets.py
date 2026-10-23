"""Own two optional desktop displays; never control the receiver or the watch."""
import os
from pathlib import Path
import subprocess
import threading
from widget_layout import LayoutStore


class Widgets:
    def __init__(self, root, port=8766):
        self.root=Path(root)
        self.port=port
        self.process=None
        self.desired={'current':False,'trend':False}
        self.lock=threading.RLock()
        self.layout=LayoutStore(root)

    def locked_states(self):
        try:
            return self.layout.locked()
        except OSError:
            # Layout is informational; dashboard visibility controls always remain available.
            return {'current':True,'trend':True}

    def status(self):
        with self.lock:
            running=self.process is not None and self.process.poll() is None
            if self.process is not None and not running:
                self.desired={'current':False,'trend':False}
            try: saved=self.layout.snapshot()
            except OSError: saved=dict(layout={},revision=0,storageError=self.layout.error)
            layout=saved['layout']
            locked={name:layout.get(name,{}).get('locked',layout.get(name,{}).get('movementLocked',False)) is True for name in ('current','trend')}
            if saved['storageError'] and not layout: locked={'current':True,'trend':True}
            return dict(self.desired, running=running, locked=locked, **saved)

    def set_visible(self, widget, visible):
        if widget not in ('current','trend') or type(visible) is not bool:
            raise ValueError('无效的小窗控制参数')
        with self.lock:
            self.status()
            if visible and (self.process is None or self.process.poll() is not None):
                executable=self.root/'desktop-widget'/'build'/'HeartRadioWidgets.exe'
                if not executable.is_file():
                    raise FileNotFoundError('请先运行 scripts/Build-Widgets.ps1 构建桌面小窗')
                if os.name!='nt':
                    raise OSError('此桌面小窗目前仅支持Windows')
                # This is a GUI executable built from this project's source. No shell is used.
                self.process=subprocess.Popen([str(executable),str(self.port),str(os.getpid()),
                                               str(self.root/'runs'/'widget-layout.json')],cwd=self.root,
                                              creationflags=subprocess.CREATE_NO_WINDOW)
            self.desired[widget]=visible
            return self.status()

    def set_locked(self, widget, locked):
        if widget not in ('current','trend') or type(locked) is not bool:
            raise ValueError('无效的小窗固定参数')
        with self.lock:
            self.layout.set_locked(widget,locked)
            return self.status()

    def save_layout(self, widget, changes, client_id, sequence):
        return self.layout.patch(widget,changes,client_id,sequence)

    def close(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            self.desired={'current':False,'trend':False}
