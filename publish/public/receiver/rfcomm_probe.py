"""Bounded RFCOMM experiment with a loopback-only command entry and real logs."""
import argparse
import json
import re
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.request

from rfcomm_win import connect
from diagnostic_log import DiagnosticLog

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / '.local-wifi' / 'rfcomm-probe.json'
SERVICE = '749b3000-6f67-4d32-96dd-4d778c27a101'
MAX_FRAME = 16384
PORT = 8769
OPS = {'ping', 'status', 'start_heart', 'stop_heart', 'remind', 'reminder_status', 'test_vibration', 'stop', 'control_state', 'inventory', 'sample', 'heart_sample','motion_window', 'cancel', 'stop_bridge', 'select_transport','sync_interaction','sync_broadcast','sync_reminder_policy'}


def frame(value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if not 0 < len(data) <= MAX_FRAME:
        raise ValueError('Frame outside bounds')
    return struct.pack('!I', len(data)) + data


def exact(stream, size, keep_waiting=None, allow_idle=False):
    result = bytearray()
    last_progress=time.monotonic()
    while len(result) < size:
        try:
            chunk = stream.recv(size - len(result))
        except TimeoutError:
            if keep_waiting is None or not keep_waiting():
                raise
            # A sleeping watch need not emit periodic Handler callbacks. Preserve
            # partial bytes, but bound an incomplete frame after it has begun.
            if not (allow_idle and not result) and time.monotonic()-last_progress>=10:
                raise TimeoutError('Incomplete RFCOMM frame stalled')
            continue
        if not chunk:
            raise ConnectionError('RFCOMM peer closed')
        result.extend(chunk)
        last_progress=time.monotonic()
    return bytes(result)


def read_frame(stream, keep_waiting=None):
    size, = struct.unpack('!I', exact(stream, 4, keep_waiting, allow_idle=True))
    if not 0 < size <= MAX_FRAME:
        raise ValueError('Frame outside bounds')
    value = json.loads(exact(stream, size, keep_waiting).decode('utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Expected an object')
    return value


class Probe:
    def __init__(self, config, output, message_observer=None, record_heart=None):
        self.message_observer = message_observer
        self.record_heart=record_heart
        self.config = config
        self.stream = DiagnosticLog(output)
        self.log_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.pending = {}
        self.client = None
        self.connected = False
        self.stopping = threading.Event()
        self.connection_serial = 0
        self.last_watch = None
        self.last_watch_monotonic = None
        self.heart_observation = None
        self.failures=0

    def log(self, kind, **fields):
        row = dict(kind=kind, pcReceiptMs=time.time_ns() // 1_000_000, pcMonotonicNs=time.monotonic_ns(), **fields)
        with self.log_lock:
            self.stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        # Routine sync requests/replies already live in the bounded diagnostic stream.
        # Console redirection is for lifecycle/errors, not a second unbounded copy of every command.
        if kind in ('experiment_start','connected','link_error','stop_disconnect','attempts_exhausted','experiment_stop'):
            print(json.dumps(row, ensure_ascii=False), flush=True)

    def state(self):
        observation = self.heart_observation if self.connected else None
        return dict(connected=self.connected, transport='rfcomm', connectionSerial=self.connection_serial,
                    failures=self.failures,
                    lastWatch=self.last_watch, stopping=self.stopping.is_set(),
                    lastWatchAgeSeconds=time.monotonic()-self.last_watch_monotonic if self.last_watch_monotonic else None,
                    diagnosticLogError=getattr(self.stream,'error',None),
                    heartObservation=dict(diagnostic=observation[0], bpmAtObservation=observation[1],
                        ageSeconds=max(0,time.monotonic()-observation[2]), cached=True,
                        connectionSerial=observation[3], sourceAgeVerified=False) if observation else None)

    def remember_heart_observation(self, row):
        observer=getattr(self,'message_observer',None)
        if observer:observer(row)
        diagnostic = row.get('heartDiagnostic')
        if isinstance(diagnostic, dict):
            self.heart_observation = (diagnostic, row.get('bpm'), time.monotonic(), self.connection_serial)

    def request(self, value):
        if not isinstance(value, dict) or set(value) - {'op', 'params', 'requestId'} or value.get('op') not in OPS:
            raise ValueError('Unknown operation or fields')
        params = value.get('params', {})
        if not isinstance(params, dict):
            raise ValueError('params must be an object')
        from reminder_protocol import FIELDS,normalize,validate_status
        allowed = {'ping': {'echo'}, 'remind': FIELDS,
                   'reminder_status': {'reminderId'}, 'sample': {'sensorId','seconds','periodUs'}, 'heart_sample':{'sensorId','seconds'}, 'motion_window':{'sensorId','seconds'}, 'cancel': {'targetId'},
                   'select_transport': {'selectionId'},'sync_interaction':{'selectionId','syncId','state'},'sync_broadcast':{'selectionId','syncId','state'},'sync_reminder_policy':{'selectionId','syncId','state'}}.get(value['op'], set())
        if set(params) - allowed:
            raise ValueError('Unknown parameters')
        if value['op']=='motion_window':
            from motion_protocol import validate_motion_window
            validate_motion_window(params)
        if value['op'] in ('sync_interaction','sync_broadcast'):
            from interaction_sync import validate_wire
            if set(params)!=allowed or any(not isinstance(params[k],str) or not re.fullmatch('[0-9a-f]{32}',params[k]) for k in ('selectionId','syncId')):raise ValueError('Invalid sync identity')
            validate_wire(params['state'])
            if value['op']=='sync_broadcast' and any(e and e['ttlMs'] is not None for e in params['state'].values()):raise ValueError('Broadcast switch has no lease')
        if value['op']=='sync_reminder_policy':
            from reminder_policy import validate_wire
            if set(params)!=allowed or any(not isinstance(params[k],str) or not re.fullmatch('[0-9a-f]{32}',params[k]) for k in ('selectionId','syncId')):raise ValueError('Invalid policy sync identity')
            validate_wire(params['state'])
        if value['op']=='heart_sample' and (set(params)!=allowed or type(params['sensorId']) is not int or type(params['seconds']) is not int or not 1<=params['seconds']<=15):raise ValueError('Invalid heart sample')
        params=dict(params)
        if value['op']=='remind':
            if 'template' not in params:params.setdefault('strength',self.config.get('defaultReminderStrength','strong'))
            params=normalize(params)
        if value['op']=='reminder_status':validate_status(params)
        request_id = value.get('requestId', uuid.uuid4().hex)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64 or not all(c.isascii() and (c.isalnum() or c in '_-') for c in request_id):
            raise ValueError('Invalid request identity')
        request = dict(id=request_id, op=value['op'], params=params)
        packet = frame(request)
        entry = dict(event=threading.Event(), result=None)
        with self.pending_lock:
            if request_id in self.pending:
                raise ValueError('Duplicate active request')
            if not self.connected or self.client is None:
                return dict(status='watch_offline')
            self.pending[request_id] = entry
            client = self.client
        try:
            with self.write_lock:
                if client is not self.client or not self.connected:
                    return dict(status='watch_offline')
                client.sendall(packet)
            self.log('command_sent', request=request, connectionSerial=self.connection_serial)
            if not entry['event'].wait(40 if value['op']=='motion_window' else 22 if value['op'] in ('sample','heart_sample') else 12):
                # The command may already have executed: never replay a timed-out reminder.
                return dict(status='outcome_unknown_timeout', requestId=request_id)
            result = entry['result']
            if value['op'] == 'stop' and result.get('status') == 'stopping':
                self.stopping.set()
            return result
        except OSError:
            client.close()
            return dict(status='outcome_unknown_disconnected',requestId=request_id)
        finally:
            with self.pending_lock:
                self.pending.pop(request_id, None)

    def disconnected(self):
        observer=getattr(self,'message_observer',None)
        if observer:observer(dict(event='disconnected'))
        with self.pending_lock:
            self.connected = False
            self.heart_observation = None
            for entry in self.pending.values():
                entry['result'] = dict(status='outcome_unknown_disconnected')
                entry['event'].set()

    def run(self, seconds, persistent=False):
        deadline = time.monotonic() + seconds if seconds is not None else float('inf')
        failures = 0
        consecutive_failures = 0
        self.log('experiment_start', maximumSeconds=seconds, service=SERVICE, authenticationRequired=True, encryptionRequired=True)
        try:
            while time.monotonic() < deadline and not self.stopping.is_set():
                retry_delay = 3
                health_stop=threading.Event()
                try:
                    self.client = (connect(self.config['watchAddress'], SERVICE, min(20,max(.1,deadline-time.monotonic())),cancel=self.stopping)
                                   if persistent else connect(self.config['watchAddress'], SERVICE, min(20, max(.1, deadline-time.monotonic()))))
                    if self.stopping.is_set():break
                    self.client.sendall(frame(dict(op='hello', version=1, token=self.config['token'])))
                    hello = read_frame(self.client)
                    if self.stopping.is_set():break
                    if hello.get('event') != 'hello' or hello.get('protocolVersion') != 1:
                        raise ValueError('Invalid watch greeting')
                    self.connection_serial += 1
                    self.last_watch = hello
                    self.last_watch_monotonic = time.monotonic()
                    self.connected = True
                    self.remember_heart_observation(hello)
                    consecutive_failures = 0
                    self.client.settimeout(1)
                    self.log('connected', hello=hello, connectionSerial=self.connection_serial)
                    if persistent:
                        connected_client=self.client
                        def health_check(client=connected_client,ended=health_stop):
                            while not ended.wait(15):
                                if self.stopping.is_set() or self.client is not client:return
                                try:
                                    result=self.request(dict(op='control_state'))
                                    if result.get('status')=='ok':continue
                                except (OSError,ValueError):pass
                                client.close();return
                        threading.Thread(target=health_check,name='rfcomm-health',daemon=True).start()
                    while time.monotonic() < deadline and not self.stopping.is_set():
                        row = read_frame(self.client,lambda:time.monotonic()<deadline and not self.stopping.is_set())
                        self.last_watch = row
                        self.last_watch_monotonic=time.monotonic()
                        self.remember_heart_observation(row)
                        if row.get('event')!='heart' or self.record_heart is None or self.record_heart():
                            self.log('watch_message', message=row, connectionSerial=self.connection_serial)
                        if row.get('event') == 'heart_state':
                            self.log('heart_state_observed', state=row.get('heartState'), message=row.get('heartStateMessage'),
                                     connectionSerial=self.connection_serial)
                        with self.pending_lock:
                            entry = self.pending.get(row.get('id'))
                            if entry:
                                entry['result'] = row
                                entry['event'].set()
                except (OSError, ValueError) as exc:
                    if self.stopping.is_set() or time.monotonic()>=deadline:
                        self.log('stop_disconnect', error=type(exc).__name__)
                    else:
                        failures += 1
                        self.failures=failures
                        # WSAENETDOWN is expected while the human-operated local
                        # radio is off. Wait within the original finite window.
                        radio_unavailable = isinstance(exc, OSError) and exc.errno == 10050
                        if radio_unavailable:
                            retry_delay = 5
                        else:
                            consecutive_failures += 1
                        if persistent:retry_delay=5 if radio_unavailable else min(30,2**min(consecutive_failures,5))
                        self.log('link_error', error=type(exc).__name__, message=str(exc), failure=failures,
                                 radioUnavailable=radio_unavailable, consecutiveFailures=consecutive_failures,
                                 retryDelaySeconds=retry_delay)
                finally:
                    health_stop.set()
                    self.disconnected()
                    if self.client is not None:
                        self.client.close()
                        self.client = None
                if consecutive_failures >= 3 and not persistent:
                    self.log('attempts_exhausted')
                    break
                self.stopping.wait(min(retry_delay, max(0, deadline-time.monotonic())))
        finally:
            self.stopping.set()
            self.log('experiment_stop', connectionCount=self.connection_serial, failures=failures)


def handler_for(probe):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def reply(self, value, status=200):
            data = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            if self.path != '/state':
                return self.reply(dict(status='not_found'), 404)
            self.reply(probe.state())
        def do_POST(self):
            if self.path != '/request' or self.headers.get('X-HeartRadio-Rfcomm') != 'request':
                return self.reply(dict(status='forbidden'), 403)
            self.connection.settimeout(3)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= MAX_FRAME:
                    raise ValueError('Invalid body size')
                data = json.loads(self.rfile.read(size).decode('utf-8'))
                self.reply(probe.request(data))
            except (OSError, ValueError) as exc:
                self.reply(dict(status='error', message=str(exc)), 400)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--watch-address', required=True)
    prepare.add_argument('--pc-address', required=True)
    prepare.add_argument('--serial', required=True)
    prepare.add_argument('--adb', default='adb')
    run = commands.add_parser('run')
    run.add_argument('--seconds', type=int, default=600)
    run.add_argument('--output', type=Path, required=True)
    request = commands.add_parser('request')
    request.add_argument('--op', choices=sorted(OPS), required=True)
    request.add_argument('--params', default='{}')
    args = parser.parse_args()
    if args.command == 'prepare':
        # Validate the device on every maintenance operation, before writing private config.
        props = {}
        for prop in ('ro.product.model', 'ro.product.brand', 'ro.build.version.sdk'):
            props[prop] = subprocess.check_output([args.adb, '-s', args.serial, 'shell', 'getprop', prop], text=True).strip()
        if list(props.values()) != ['OW20W2', 'OPPO', '27']:
            raise ValueError('Unexpected watch; no config written')
        from rfcomm_win import make_address
        make_address(args.watch_address, SERVICE)
        make_address(args.pc_address, SERVICE)
        if CONFIG.exists():
            config = json.loads(CONFIG.read_text(encoding='utf-8'))
            if config['watchAddress'].upper() != args.watch_address.upper() or config['allowPc'].upper() != args.pc_address.upper():
                raise ValueError('Existing experiment identifies different peers')
        else:
            config = dict(watchAddress=args.watch_address.upper(), allowPc=args.pc_address.upper(), token=secrets.token_hex(32))
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(json.dumps(config), encoding='utf-8')
        completed = subprocess.run([args.adb, '-s', args.serial, 'shell', 'run-as', 'dev.local.watch2.hrprobe',
                                    'tee', 'files/rfcomm-probe.json'], input=json.dumps(config).encode('utf-8'), capture_output=True, timeout=10)
        if completed.returncode:
            raise RuntimeError('Could not write app-private experiment configuration')
        print('Experimental peer configuration prepared; token omitted.')
    elif args.command == 'request':
        payload = dict(op=args.op, params=json.loads(args.params))
        req = urllib.request.Request(f'http://127.0.0.1:{PORT}/request', data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                     headers={'Content-Type': 'application/json', 'X-HeartRadio-Rfcomm': 'request'})
        with urllib.request.urlopen(req, timeout=16) as response:
            print(response.read().decode('utf-8'))
    else:
        if not 1 <= args.seconds <= 3600:
            parser.error('seconds must be 1..3600')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        probe = Probe(json.loads(CONFIG.read_text(encoding='utf-8')), args.output)
        http = ThreadingHTTPServer(('127.0.0.1', PORT), handler_for(probe))
        http.daemon_threads = True
        threading.Thread(target=http.serve_forever, daemon=True).start()
        try:
            probe.run(args.seconds)
        finally:
            http.shutdown()
            http.server_close()
            probe.stream.close()


if __name__ == '__main__':
    main()
