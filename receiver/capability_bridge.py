"""Opt-in paired TLS bridge for bounded public-API tests; heart-rate v1 is untouched."""
import argparse
import asyncio
import concurrent.futures
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import ssl
import struct
import threading
import time
import uuid
from control import Deadline, read_settings

from lan import LanAdapter, private_ipv4
from discovery import Discovery, discovery_socket

MAX_FRAME = 65536
OPS = {'inventory', 'status', 'sample', 'cached_location', 'locate', 'cancel'}


def frame(value):
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if not 0 < len(payload) <= MAX_FRAME:
        raise ValueError('Frame size out of range')
    return struct.pack('!I', len(payload)) + payload


async def read_frame(reader):
    size = struct.unpack('!I', await reader.readexactly(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError('Frame size out of range')
    value = json.loads(await reader.readexactly(size))
    if not isinstance(value, dict):
        raise ValueError('Expected object')
    return value


def validate_request(value):
    if not isinstance(value, dict) or set(value) - {'op', 'params', 'requestId'} or value.get('op') not in OPS:
        raise ValueError('Unknown request operation or fields')
    params = value.get('params', {})
    if not isinstance(params, dict):
        raise ValueError('params must be an object')
    allowed = {'sample': {'sensorId', 'seconds', 'periodUs'}, 'locate': {'provider', 'seconds'},
               'cancel': {'targetId'}}.get(value['op'], set())
    if set(params) - allowed:
        raise ValueError('Unknown parameters')
    if value['op'] == 'sample' and type(params.get('sensorId')) is not int:
        raise ValueError('sensorId must be an integer')
    for key, low, high in [('seconds', 1, 180 if value['op'] == 'locate' else 60), ('periodUs', 100000, 1000000)]:
        if key in params and (type(params[key]) is not int or not low <= params[key] <= high):
            raise ValueError(f'{key} outside allowed range')
    if 'provider' in params and params['provider'] not in ('gps', 'network', 'passive', 'fused'):
        raise ValueError('Unknown location provider')
    if 'targetId' in params and (not isinstance(params['targetId'], str) or len(params['targetId']) > 64):
        raise ValueError('Invalid targetId')
    if value['op'] == 'cancel' and not re.fullmatch('[A-Za-z0-9_-]{1,64}', str(params.get('targetId', ''))):
        raise ValueError('Targeted cancellation requires targetId')
    result = dict(op=value['op'], params=params)
    if 'requestId' in value:
        if type(value['requestId']) is not str or not re.fullmatch('[0-9a-f]{32}', value['requestId']):
            raise ValueError('Invalid requestId')
        result['requestId'] = value['requestId']
    return result


class Bridge:
    def __init__(self, token, log_path, allow_watch='', instance_id=None):
        self.token, self.allow_watch = token, allow_watch
        self.instance_id = instance_id or uuid.uuid4().hex
        self.deadline = None
        self.duration_limit = None
        self.duration_sync_sent = None
        self.listen_host = None
        self.writer = None
        self.pending = {}
        self.pending_operations = {}
        self.connections = set()
        self.connection_serial = 0
        self.connected_at_ms = None
        self.last_disconnect_ms = None
        self.last_disconnect_reason = None
        self.watch_lifecycle = None
        self.watch_reported_ms = None
        self.log_file = log_path.open('x', encoding='utf-8', buffering=1)
        self.write_lock = asyncio.Lock()

    def log(self, kind, **fields):
        self.log_file.write(json.dumps(dict(kind=kind, pcReceiptMs=time.time_ns() // 1000000,
                                           pcMonotonicNs=time.monotonic_ns(), **fields), ensure_ascii=False, allow_nan=False) + '\n')

    def state(self):
        return dict(status='connected' if self.writer else 'watch_offline', connected=self.writer is not None,
                    instanceId=self.instance_id,
                    serverRemainingSeconds=max(0, self.deadline - time.monotonic()) if self.deadline is not None else None,
                    serverLimit=self.duration_limit.fields() if self.duration_limit else None,
                    watchDurationSync=self.sync_state(),
                    listenHost=self.listen_host,
                    connectionSerial=self.connection_serial, connectedAtMs=self.connected_at_ms,
                    lastDisconnectMs=self.last_disconnect_ms, lastDisconnectReason=self.last_disconnect_reason,
                    watchLifecycle=self.watch_lifecycle,
                    watchReportAgeSeconds=(time.time_ns() // 1000000 - self.watch_reported_ms) / 1000
                    if self.writer and self.watch_reported_ms is not None else None)

    def sync_state(self):
        report = self.watch_lifecycle or {}
        return dict(requested=report.get('followComputer') is True,
                    supported=report.get('durationSyncVersion') == 1,
                    applied=bool(self.writer and self.duration_limit and report.get('followComputer') is True and
                                 report.get('durationSyncRevision') == self.duration_limit.revision and
                                 report.get('durationSyncInstanceId') == self.instance_id))

    async def sync_duration(self, expected_writer=None):
        writer = expected_writer or self.writer
        report = self.watch_lifecycle or {}
        if report.get('followComputer') is not True:
            self.duration_sync_sent = None
            return
        if report.get('durationSyncVersion') != 1 or not writer or writer is not self.writer or not self.duration_limit:
            return
        key = (self.instance_id, self.duration_limit.revision, self.connection_serial)
        if key == self.duration_sync_sent: return
        limit = self.duration_limit
        remaining = max(0, int((limit.ends - time.monotonic()) * 1000)) if limit.ends is not None else None
        value = dict(id='duration_set_' + uuid.uuid4().hex, op='set_standby_duration',
                     params=dict(seconds=limit.seconds, remainingMs=remaining, revision=limit.revision, instanceId=self.instance_id))
        try:
            await asyncio.wait_for(self.send(value, writer), 2)
            self.duration_sync_sent = key
            self.log('watch_duration_sent', **value['params'])
        except (ConnectionError, OSError, asyncio.TimeoutError):
            writer.close()

    async def send(self, value, expected_writer=None):
        async with self.write_lock:
            if self.writer is None or expected_writer is not None and self.writer is not expected_writer:
                raise ConnectionError('watch_offline')
            self.writer.write(frame(value))
            await self.writer.drain()

    async def control(self, action):
        writer = self.writer
        if writer is None:
            return dict(status='watch_offline')
        if action == 'disconnect':
            self.log('test_disconnect', connectionSerial=self.connection_serial)
            writer.close()
            return dict(status='disconnect_requested')
        if action != 'stop_watch':
            raise ValueError('Unknown control')
        try:
            await self.send(dict(id=uuid.uuid4().hex, op='stop_bridge'), writer)
            self.log('watch_stop_sent', connectionSerial=self.connection_serial)
            return dict(status='stop_sent')
        except (ConnectionError, OSError):
            return dict(status='disconnected')

    async def request(self, value):
        value = validate_request(value)
        if self.writer is None:
            return dict(status='watch_offline')
        if len(self.pending) >= 8:
            return dict(status='busy')
        if value['op'] == 'cancel':
            target = value['params']['targetId']
            if target not in self.pending or self.pending[target].done() or self.pending_operations.get(target) not in ('sample', 'locate'):
                return dict(status='not_active')
        request_id = value.pop('requestId', None) or uuid.uuid4().hex
        if request_id in self.pending: return dict(status='duplicate_request')
        writer = self.writer
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        self.pending_operations[request_id] = value['op']
        begin = time.monotonic()
        request = dict(id=request_id, **value)
        self.log('remote_request', request=request)
        try:
            await self.send(request, writer)
            result = await asyncio.wait_for(future, value['params'].get('seconds', 5) + 8)
            result['pcRoundTripMs'] = (time.monotonic() - begin) * 1000
            result['transport'] = 'wifi_tls'
            result['adbUsedForRequest'] = False
            self.log('remote_result', op=value['op'], result=result)
            return result
        except asyncio.TimeoutError:
            try:
                await self.send(dict(id=uuid.uuid4().hex, op='cancel', params=dict(targetId=request_id)), writer)
            except (ConnectionError, OSError):
                pass
            self.log('request_timeout', requestId=request_id)
            return dict(status='request_timeout', id=request_id)
        except (ConnectionError, OSError) as error:
            return dict(status='disconnected', id=request_id, error=type(error).__name__)
        finally:
            self.pending.pop(request_id, None)
            self.pending_operations.pop(request_id, None)

    async def connected(self, reader, writer):
        task = asyncio.current_task()
        self.connections.add(task)
        approved = False
        ping_task = None
        peer = writer.get_extra_info('peername')[0]
        try:
            if self.writer or (self.allow_watch and peer != self.allow_watch) or not private_ipv4(peer):
                return
            supplied = await asyncio.wait_for(reader.readexactly(32), 5)
            if not hmac.compare_digest(supplied, self.token):
                self.log('authentication_rejected')
                writer.write(b'\x00'); await writer.drain()
                return
            if self.writer:
                return
            self.writer = writer; approved = True
            self.connection_serial += 1
            self.connected_at_ms = time.time_ns() // 1000000
            self.watch_lifecycle = None; self.watch_reported_ms = None
            self.duration_sync_sent = None
            writer.write(b'\x01'); await writer.drain()
            self.log('bridge_connected', target=peer, tls=writer.get_extra_info('ssl_object').version(),
                     connectionSerial=self.connection_serial)
            async def ping():
                while True:
                    await asyncio.sleep(10)
                    await self.send(dict(id='ping_' + uuid.uuid4().hex, op='ping'), writer)
            ping_task = asyncio.create_task(ping())
            while True:
                result = await asyncio.wait_for(read_frame(reader), 35)
                request_id = result.get('id')
                if request_id in ('bridge_session', 'standby_state') or str(request_id).startswith(('ping_', 'duration_set_')):
                    if 'leaseRemainingMs' in result:
                        keys = ('state', 'reason', 'attempt', 'sessionId', 'leaseRemainingMs', 'retryAfterMs',
                                'wallMs', 'elapsedMs', 'screenInteractive', 'leaseUnlimited', 'leaseDurationMs',
                                'durationSyncVersion', 'followComputer', 'durationSyncRevision', 'durationSyncInstanceId')
                        self.watch_lifecycle = {key: result.get(key) for key in keys}
                        self.watch_reported_ms = time.time_ns() // 1000000
                        if request_id == 'bridge_session':
                            self.log('watch_session', lifecycle=self.watch_lifecycle)
                        if str(request_id).startswith('duration_set_'):
                            self.log('watch_duration_ack', status=result.get('status'), lifecycle=self.watch_lifecycle)
                        await self.sync_duration(writer)
                elif request_id in self.pending and not self.pending[request_id].done():
                    self.pending[request_id].set_result(result)
                elif not str(request_id).startswith('ping_'):
                    # Retain late cleanup/results as evidence without satisfying a different request.
                    self.log('late_or_unmatched_result', result=result)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, OSError, ValueError) as error:
            if approved:
                self.last_disconnect_ms = time.time_ns() // 1000000
                self.last_disconnect_reason = type(error).__name__
                self.log('bridge_disconnected', reason=type(error).__name__)
        finally:
            if ping_task:
                ping_task.cancel()
                await asyncio.gather(ping_task, return_exceptions=True)
            if approved:
                self.writer = None
                for future in list(self.pending.values()):
                    if not future.done():
                        future.set_exception(ConnectionError('watch_disconnected'))
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            self.connections.discard(task)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if (self.path != '/state' or self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}' or
                self.headers.get('Origin') is not None):
            self.send_error(403); return
        future = asyncio.run_coroutine_threadsafe(self.connection_state(), self.server.loop)
        try:
            result = future.result(timeout=3)
        except concurrent.futures.TimeoutError:
            future.cancel(); self.send_error(504); return
        self.json_reply(result)

    async def connection_state(self):
        return self.server.bridge.state()

    def json_reply(self, result):
        payload = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        self.connection.settimeout(5)
        expected = f'127.0.0.1:{self.server.server_port}'
        if (self.path not in ('/request', '/control') or self.headers.get('Host') != expected or
                self.headers.get('Origin') is not None or
                self.headers.get('X-HeartRadio-Probe') != 'request' or
                self.headers.get_content_type() != 'application/json'):
            # Drain only a bounded, declared body before closing. On Windows an
            # unread POST body can reset the socket and hide the rejection reply.
            try:
                rejected_length = int(self.headers.get('Content-Length', '0'))
                if 0 < rejected_length <= 8192:
                    self.rfile.read(rejected_length)
            except (OSError, ValueError):
                pass
            self.send_error(403); return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                raise ValueError('Request size out of range')
            value = json.loads(self.rfile.read(length))
            if self.path == '/control':
                if not isinstance(value, dict) or set(value) != {'action'} or value['action'] not in ('disconnect', 'stop_watch'):
                    raise ValueError('Invalid control')
                operation = self.server.bridge.control(value['action'])
            else:
                operation = self.server.bridge.request(validate_request(value))
            future = asyncio.run_coroutine_threadsafe(operation, self.server.loop)
            wait_seconds = max(75, value.get('params', {}).get('seconds', 5) + 15) if self.path == '/request' else 75
            result = future.result(timeout=wait_seconds)
        except (ValueError, KeyError, TypeError):
            self.send_error(400); return
        except concurrent.futures.TimeoutError:
            future.cancel(); self.send_error(504); return
        self.json_reply(result)

    def log_message(self, *args):
        pass


async def run(args):
    config = json.loads((args.directory / 'config.json').read_text(encoding='utf-8'))
    adapter = None if args.host else LanAdapter(args.directory, config['host'])
    host = args.host or adapter.address()
    if not host or not private_ipv4(host):
        raise ValueError('Expected one current private LAN address')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bridge = Bridge(bytes.fromhex(config['token']), args.output, args.allow_watch, args.instance_id)
    settings_path = getattr(args, 'settings', None)
    settings = read_settings(settings_path) if settings_path else None
    seconds = args.seconds if args.seconds is not None else (settings['seconds'] if settings else 1800)
    bridge.duration_limit = Deadline(seconds, settings)
    bridge.deadline = bridge.duration_limit.ends
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.directory / 'cert.pem', args.directory / 'key.pem')
    server = None
    discovery_transport = None
    async def listen(address):
        nonlocal server, discovery_transport
        if server:
            server.close(); await server.wait_closed(); server = None
        if discovery_transport:
            discovery_transport.close(); discovery_transport = None
            await asyncio.sleep(0)
        if bridge.writer:
            bridge.writer.close()
        bridge.listen_host = None
        if address:
            if not private_ipv4(address):
                raise ValueError('Expected one private LAN address')
            server = await asyncio.start_server(bridge.connected, address, args.port, ssl=context,
                                               ssl_handshake_timeout=5, ssl_shutdown_timeout=2, limit=MAX_FRAME + 4)
            sock = discovery_socket(address, args.port)
            try:
                discovery_transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                    lambda: Discovery(bridge.token, config['pin'], args.port, 'HRBRIDGEDISC1', 'HRBRIDGEHERE1'), sock=sock)
            except BaseException:
                sock.close(); raise
            bridge.listen_host = address
        bridge.log('bridge_listener_changed', host=address, port=args.port, discovery=True)
    try:
        await listen(host)
    except BaseException:
        if server: server.close(); await server.wait_closed()
        bridge.log_file.close()
        raise
    api = ThreadingHTTPServer(('127.0.0.1', args.api_port), Handler)
    api.daemon_threads = True
    api.bridge, api.loop = bridge, asyncio.get_running_loop()
    thread = threading.Thread(target=api.serve_forever, daemon=True); thread.start()
    bridge.log('bridge_server_start', host=host, port=args.port, apiPort=args.api_port,
               transport='wifi_tls', synthetic=False, maxSeconds=seconds)
    bridge.log('bridge_limit', **bridge.duration_limit.fields())
    print(json.dumps(dict(status='ready', host=host, port=args.port, apiPort=args.api_port,
                          instanceId=bridge.instance_id)), flush=True)
    try:
        next_network_check = time.monotonic() + 5
        settings_error = False
        while not args.stop_file.exists():
            if settings_path:
                try:
                    if bridge.duration_limit.apply(read_settings(settings_path)):
                        bridge.deadline = bridge.duration_limit.ends
                        bridge.log('bridge_limit', **bridge.duration_limit.fields())
                        await bridge.sync_duration()
                    settings_error = False
                except (OSError, ValueError, KeyError, TypeError):
                    if not settings_error: bridge.log('settings_error', reason='Unreadable duration settings; keeping previous limit')
                    settings_error = True
            if bridge.duration_limit.expired(): break
            if adapter and time.monotonic() >= next_network_check:
                next_network_check = time.monotonic() + 5
                try:
                    address = adapter.address()
                    if address != host or (address and bridge.listen_host is None):
                        await listen(address)
                        host = address
                except OSError as error:
                    bridge.log('bridge_listener_error', reason=type(error).__name__)
                    host = None
            await asyncio.sleep(.25)
    finally:
        # Normal stop/expiry sends an explicit stop. A crash has no chance to send it and is retried.
        keep_standby = args.stop_file.exists() and args.stop_file.read_text(encoding='utf-8-sig').strip() == 'keep_watch_standby'
        bridge.log('bridge_shutdown_mode', keepWatchStandby=keep_standby)
        if not keep_standby:
            result = await bridge.control('stop_watch')
            bridge.log('watch_stop_result', **result)
        await asyncio.sleep(.2)
        api.shutdown(); api.server_close()
        if bridge.writer:
            bridge.writer.close()
        if discovery_transport: discovery_transport.close()
        if server: server.close(); await server.wait_closed()
        remaining = list(bridge.connections)
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
        bridge.log('bridge_server_stop'); bridge.log_file.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('.local-wifi'))
    parser.add_argument('--host')
    parser.add_argument('--allow-watch', default='')
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--api-port', type=int, default=8768)
    parser.add_argument('--seconds', type=float, default=None)
    parser.add_argument('--settings', type=Path, help='live PC duration preferences; opt-in watch synchronization uses remaining time')
    parser.add_argument('--instance-id', default=None)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stop-file', type=Path, required=True)
    args = parser.parse_args()
    if (args.seconds is not None and (not math.isfinite(args.seconds) or args.seconds < 0 or
                                     args.seconds > 31536000 or 0 < args.seconds < 1)) or args.stop_file.exists():
        parser.error('Use 0 (unlimited) or 1..31536000 seconds and a new stop-file path')
    asyncio.run(run(args))


if __name__ == '__main__':
    main()
