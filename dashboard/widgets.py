"""Own two optional desktop displays; never control the receiver or the watch."""
import os
import json
from pathlib import Path
import subprocess
import threading
import uuid


class Widgets:
    def __init__(self, root, port=8766):
        self.root=Path(root)
        self.port=port
        self.process=None
        self.desired={'current':False,'trend':False}
        self.lock=threading.RLock()

    def locked_states(self):
        try:
            saved=json.loads((self.root/'runs'/'widget-layout.json').read_text(encoding='utf-8-sig'))
            return {name: saved.get(name,{}).get('locked',saved.get(name,{}).get('movementLocked',False)) is True
                    for name in ('current','trend')}
        except FileNotFoundError:
            return {'current':False,'trend':False}
        except (OSError,ValueError,AttributeError):
            # Layout is informational; dashboard visibility controls always remain available.
            return {'current':True,'trend':True}

    def status(self):
        with self.lock:
            running=self.process is not None and self.process.poll() is None
            if self.process is not None and not running:
                self.desired={'current':False,'trend':False}
            return dict(self.desired, running=running, locked=self.locked_states())

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
            path=self.root/'runs'/'widget-layout.json'
            try:
                saved=json.loads(path.read_text(encoding='utf-8-sig'))
            except FileNotFoundError:
                saved={}
            if not isinstance(saved,dict) or not isinstance(saved.get(widget,{}),dict):
                raise ValueError('小窗布局文件格式无效')
            saved.setdefault(widget,{})['locked']=locked
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
            try:
                temporary.write_text(json.dumps(saved,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
                os.replace(temporary,path)
            finally:
                temporary.unlink(missing_ok=True)
            return self.status()

    def close(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            self.desired={'current':False,'trend':False}
