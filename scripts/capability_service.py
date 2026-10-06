"""Own the daily PC bridge without ADB, duplicate listeners or unrelated process termination."""
import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

from resource_native import Windows
from capability_duration import MAX_SECONDS, duration_path, read_duration, save_settings

ROOT = Path(__file__).resolve().parents[1]


class ServiceError(OSError):
    pass


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return None


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class CapabilityService:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.runs = self.root / 'runs'
        self.metadata = self.runs / 'capability-service.json'
        self.settings = self.runs / 'capability-service-settings.json'
        self._windows = None

    @property
    def windows(self):
        if self._windows is None:
            self._windows = Windows()
        return self._windows

    @contextmanager
    def locked(self):
        try:
            mutex = self.windows.mutex('Local\\HeartRadioCapabilityService')
        except RuntimeError as error:
            raise ServiceError('按需服务正在启动或停止，请稍后重试。') from error
        with mutex:
            yield

    def preference(self):
        value = read_json(self.settings)
        if value is None:
            return dict(enabledOnStart=False)
        if not isinstance(value, dict) or set(value) != {'enabledOnStart'} or type(value['enabledOnStart']) is not bool:
            raise ServiceError('按需启动设置损坏，请检查 runs/capability-service-settings.json。')
        return value

    def save(self, request):
        if not isinstance(request, dict) or set(request) != {'enabledOnStart'} or type(request['enabledOnStart']) is not bool:
            raise ValueError('只接受 enabledOnStart 布尔值。')
        with self.locked():
            atomic_json(self.settings, request)
        return self.status()

    def info(self):
        value = read_json(self.metadata)
        if value is None:
            return None
        if (not isinstance(value, dict) or type(value.get('pid')) is not int or value['pid'] <= 0 or
                not re.fullmatch(r'[0-9]+', str(value.get('creationTicks', ''))) or
                not re.fullmatch(r'[0-9a-f]{32}', str(value.get('instanceId', ''))) or
                not re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{8}', str(value.get('runId', ''))) or
                not isinstance(value.get('executable'), str) or not Path(value['executable']).is_absolute() or
                type(value.get('seconds')) not in (int, float) or not math.isfinite(value['seconds']) or not 0 <= value['seconds'] <= MAX_SECONDS or
                (value['seconds'] == 0 and value.get('expiresAtMs') is not None) or
                (value['seconds'] > 0 and type(value.get('expiresAtMs')) is not int)):
            raise ServiceError('按需进程索引损坏，未执行停止操作。')
        return value

    def directory(self, info):
        return self.runs / 'capability-service' / info['runId']

    def owned(self, info):
        if not info:
            return False
        try:
            process = self.windows.process(info['pid'])
        except OSError as error:
            if error.errno == 5:
                raise ServiceError('无法核对按需进程身份；请使用同一用户检查服务。') from error
            return False
        return (process['creationTicks'] == int(info['creationTicks']) and
                Path(process['executable']).resolve() == Path(info['executable']).resolve())

    @staticmethod
    def bridge_state():
        # Read PC connection metadata only; polling sends no watch sensor request.
        try:
            with urllib.request.urlopen('http://127.0.0.1:8768/state', timeout=.6) as response:
                raw = response.read(8193)
            value = json.loads(raw) if len(raw) <= 8192 else None
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def status(self):
        result = dict(self.preference(), running=False, state='stopped', connected=False,
                      remainingSeconds=0, maxSeconds=MAX_SECONDS, configuredSeconds=None)
        try:
            settings = read_duration(self.root)
            result['configuredSeconds'] = settings['seconds']
            result['durationSettings'] = settings
        except (OSError, ValueError, KeyError, TypeError) as error:
            result['settingsError'] = str(error)
        info = self.info()
        if self.owned(info):
            connection = self.bridge_state()
            ready = connection and connection.get('instanceId') == info['instanceId']
            result.update(running=True, state='ready' if ready else 'starting', pid=info['pid'],
                          instanceId=info['instanceId'], expiresAtMs=info['expiresAtMs'],
                          durationSeconds=info['seconds'],
                          durationApplied=False,
                          remainingSeconds=max(0, (info['expiresAtMs'] - time.time_ns() // 1000000) / 1000) if info['expiresAtMs'] is not None else None)
            if ready:
                result['connected'] = connection.get('connected') is True
                result['watch'] = connection
                if 'listenHost' in connection and connection['listenHost'] is None:
                    result['state'] = 'network_wait'
                remaining = connection.get('serverRemainingSeconds')
                if type(remaining) in (int, float):
                    result['remainingSeconds'] = max(0, remaining)
                limit = connection.get('serverLimit')
                if isinstance(limit, dict):
                    result.update(durationSeconds=limit['seconds'], expiresAtMs=limit['endsMs'],
                                  remainingSeconds=remaining,
                                  durationApplied=limit['revision'] == result.get('durationSettings', {}).get('revision'))
                result['durationSupported'] = isinstance(limit, dict)
        elif info:
            stopped = (self.directory(info) / 'stop.request').exists()
            result['state'] = 'stopped' if stopped else 'expired' if info['expiresAtMs'] is not None and time.time_ns() // 1000000 >= info['expiresAtMs'] else 'exited'
        if not result['running'] and self.bridge_state() is not None:
            result['state'] = 'port_conflict'
        return result

    def duration_settings(self):
        return read_duration(self.root)

    def save_duration(self, request):
        with self.locked():
            value = save_settings(duration_path(self.root), request)
            info = self.info()
            if self.owned(info):
                # Ownership metadata follows the saved preference; the bridge reports actual application.
                info.update(seconds=value['seconds'], expiresAtMs=int(value['savedMs'] + value['seconds'] * 1000) if value['seconds'] else None)
                atomic_json(self.metadata, info)
        return value

    @staticmethod
    def check_ports():
        for port, kind in ((8767, socket.SOCK_STREAM), (8768, socket.SOCK_STREAM), (8767, socket.SOCK_DGRAM)):
            with socket.socket(socket.AF_INET, kind) as probe:
                if os.name == 'nt':
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    probe.bind(('0.0.0.0', port))
                except OSError as error:
                    raise ServiceError(f'端口 {port} 已占用；请先停止已有接口测试或相关服务。不会结束其他进程。') from error

    def start(self, seconds=None, if_enabled=False):
        if seconds is None: seconds = read_duration(self.root)['seconds']
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 <= seconds <= MAX_SECONDS:
            raise ValueError('电脑按需服务时长为0（不限时）或1秒～365天。')
        if 0 < seconds < 1: raise ValueError('电脑按需服务限时最短1秒。')
        with self.locked():
            if if_enabled and not self.preference()['enabledOnStart']:
                return dict(self.status(), action='disabled')
            current = self.status()
            if current['running']:
                return dict(current, action='reused')
            if not all((self.root / '.local-wifi' / name).is_file() for name in ('config.json', 'cert.pem', 'key.pem')):
                raise ServiceError('尚未完成电脑配对配置，请先按连接指南进行首次配置。')
            self.check_ports()
            instance = uuid.uuid4().hex
            run_id = time.strftime('%Y%m%d-%H%M%S-') + instance[:8]
            directory = self.runs / 'capability-service' / run_id
            directory.mkdir(parents=True)
            stop = directory / 'stop.request'
            command = [sys.executable, '-u', str(self.root / 'receiver' / 'capability_bridge.py'),
                       '--directory', str(self.root / '.local-wifi'), '--seconds', str(seconds),
                       '--settings', str(duration_path(self.root)),
                       '--instance-id', instance, '--output', str(directory / 'pc-bridge.jsonl'), '--stop-file', str(stop)]
            with (directory / 'stdout.txt').open('wb') as out, (directory / 'stderr.txt').open('wb') as err:
                child = subprocess.Popen(command, cwd=self.root, stdout=out, stderr=err,
                                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            registered = False
            try:
                process = self.windows.process(child.pid)
                info = dict(pid=child.pid, creationTicks=str(process['creationTicks']), executable=process['executable'],
                            runId=run_id, instanceId=instance, seconds=seconds,
                            expiresAtMs=int(time.time_ns() // 1000000 + seconds * 1000) if seconds else None)
                # Register ownership before waiting so another stop can recover a partial start.
                atomic_json(self.metadata, info)
                registered = True
                until = time.monotonic() + 8
                while time.monotonic() < until:
                    if child.poll() is not None:
                        raise ServiceError(f'按需服务启动失败，请查看 {directory / "stderr.txt"}。')
                    connection = self.bridge_state()
                    if connection and connection.get('instanceId') == instance:
                        return dict(self.status(), action='started')
                    time.sleep(.1)
                raise ServiceError(f'按需服务未按时就绪，请查看 {directory / "stderr.txt"}。')
            except BaseException:
                stop.touch(exist_ok=True)
                # Only this just-created child may be terminated after a failed startup.
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.terminate(); child.wait(timeout=3)
                if not registered:
                    raise ServiceError('无法登记按需服务进程；本次创建的进程已停止。')
                raise

    def stop(self):
        with self.locked():
            info = self.info()
            if not self.owned(info):
                return dict(self.status(), action='already_stopped', watchStopStatus='not_sent')
            directory = self.directory(info)
            (directory / 'stop.request').touch(exist_ok=True)
            until = time.monotonic() + 8
            while self.owned(info) and time.monotonic() < until:
                time.sleep(.1)
            if self.owned(info):
                raise ServiceError('停止请求已写入，但电脑按需服务尚未退出，请稍后查看；未强制终止进程。')
            watch_status = 'not_confirmed'
            try:
                for line in (directory / 'pc-bridge.jsonl').read_text(encoding='utf-8').splitlines()[-20:]:
                    row = json.loads(line)
                    if row.get('kind') == 'watch_stop_result':
                        watch_status = row.get('status', 'not_confirmed')
            except (OSError, ValueError):
                pass
            return dict(self.status(), action='stopped', watchStopStatus=watch_status)

    def control(self, request):
        if not isinstance(request, dict) or set(request) != {'action'} or request['action'] not in ('start', 'stop'):
            raise ValueError('只接受 start 或 stop 操作。')
        return self.start() if request['action'] == 'start' else self.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'start-if-enabled', 'stop', 'status'])
    parser.add_argument('--seconds', type=float, default=None)
    args = parser.parse_args()
    service = CapabilityService()
    try:
        value = (service.start(args.seconds, args.action == 'start-if-enabled') if args.action.startswith('start')
                 else service.stop() if args.action == 'stop' else service.status())
    except (OSError, ValueError) as error:
        print(json.dumps(dict(error=str(error)), ensure_ascii=False), flush=True)
        return 1
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
