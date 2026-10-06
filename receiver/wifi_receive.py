"""TLS receiver for the paired watch, with live duration settings and LAN discovery."""
import argparse
import asyncio
import hmac
import ipaddress
import json
from pathlib import Path
import ssl
import time
import math
from protocol import decode
from control import Deadline, read_settings
from discovery import Discovery, discovery_socket
from lan import LanAdapter, private_ipv4


async def run(args):
    config = json.loads((args.directory / 'config.json').read_text(encoding='utf-8'))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.directory / 'cert.pem', args.directory / 'key.pem')
    token = bytes.fromhex(config['token'])
    active = set()
    tasks = set()
    previous = None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8', buffering=1) as out:
        def log(kind, **fields):
            row = dict(kind=kind, pcReceiptMs=time.time_ns() // 1_000_000,
                       pcMonotonicNs=time.monotonic_ns(), **fields)
            line = json.dumps(row, ensure_ascii=False)
            out.write(line + '\n')
            print(line, flush=True)

        async def connected(reader, writer):
            nonlocal previous
            task = asyncio.current_task()
            tasks.add(task)
            peer = writer.get_extra_info('peername')[0]
            approved = False
            try:
                if (args.allow_watch and peer != args.allow_watch) or active or (not private_ipv4(peer) and peer != '127.0.0.1'):
                    return
                # Authenticate before recording any packet. Never log the token.
                supplied = await asyncio.wait_for(reader.readexactly(32), 4)
                if not hmac.compare_digest(supplied, token):
                    log('authentication_rejected')
                    return
                if active:
                    return
                active.add(writer); approved = True
                writer.write(b'\x01'); await writer.drain()
                log('wifi_connected', target=peer, host=writer.get_extra_info('sockname')[0],
                    tls=writer.get_extra_info('ssl_object').version())
                while True:
                    payload = await asyncio.wait_for(reader.readexactly(20), 20)
                    packet = decode(payload)
                    current = packet['session'], packet['sequence']
                    step = None if previous is None or previous[0] != current[0] else (current[1] - previous[1]) & 0xffff
                    previous = current
                    log('packet', hex=payload.hex(), packet=packet, sequenceStep=step)
                    writer.write(b'\x01'); await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError, ValueError) as exc:
                if approved:
                    log('wifi_disconnected', reason=type(exc).__name__)
            finally:
                active.discard(writer)
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, ssl.SSLError):
                    pass
                tasks.discard(task)

        bind_host = getattr(args, 'bind_host', None) or config['host']
        adapter = LanAdapter(args.directory, config['host']) if getattr(args, 'follow_lan', False) else None
        if adapter:
            bind_host = adapter.address()
        settings_path = getattr(args, 'settings', None)
        settings = read_settings(settings_path) if settings_path else None
        seconds = args.seconds if args.seconds is not None else (settings['seconds'] if settings else 0)
        deadline = Deadline(seconds, settings)
        server = None
        discovery_transport = None
        async def listen(host):
            nonlocal server, discovery_transport
            if server:
                server.close()
                await server.wait_closed()
                server = None
            if discovery_transport:
                discovery_transport.close()
                discovery_transport = None
                await asyncio.sleep(0)
            for writer in list(active):
                writer.close()
            if host:
                if not private_ipv4(host) and host != '127.0.0.1':
                    raise ValueError('接收器只允许绑定一个局域网私有IPv4地址')
                server = await asyncio.start_server(connected, host, config['port'], ssl=context,
                                                   ssl_handshake_timeout=4, ssl_shutdown_timeout=2, limit=1024)
                if getattr(args, 'discovery', False):
                    sock = discovery_socket(host, config['port'])
                    try:
                        discovery_transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                            lambda: Discovery(token, config['pin'], config['port']), sock=sock)
                    except BaseException:
                        sock.close()
                        raise
            log('listener_changed', host=host, port=config['port'])
        try:
            await listen(bind_host)
            log('server_start', host=bind_host, port=config['port'], allowWatch=args.allow_watch,
                seconds=seconds, transport='wifi_tls', synthetic=False)
            log('receiver_limit', **deadline.fields())
            settings_error = False
            next_network_check = time.monotonic() + 5
            while True:
                stop_file = getattr(args, 'stop_file', None)
                if stop_file is not None and stop_file.exists():
                    log('stop_requested')
                    break
                if settings_path:
                    try:
                        if deadline.apply(read_settings(settings_path)):
                            log('receiver_limit', **deadline.fields())
                        settings_error = False
                    except (OSError, ValueError, KeyError, TypeError):
                        if not settings_error:
                            log('settings_error', reason='Unreadable settings; keeping previous limit')
                        settings_error = True
                if deadline.expired():
                    log('capture_window_complete')
                    break
                if adapter and time.monotonic() >= next_network_check:
                    next_network_check = time.monotonic() + 5
                    try:
                        host = adapter.address()
                        if host != bind_host or (host and server is None):
                            await listen(host)
                            bind_host = host
                    except OSError as error:
                        log('listener_error', reason=type(error).__name__)
                        bind_host = None
                await asyncio.sleep(.25)
        finally:
            if discovery_transport:
                discovery_transport.close()
            if server:
                server.close()
            for writer in list(active):
                writer.close()
            pending = list(tasks)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if server:
                await server.wait_closed()
            log('server_stop')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, default=Path('.local-wifi'))
    p.add_argument('--allow-watch', default='', help='optional fixed source IPv4 restriction')
    p.add_argument('--bind-host', help='one local private IPv4 listener')
    p.add_argument('--follow-lan', action='store_true', help='follow the selected Windows LAN adapter')
    p.add_argument('--discovery', action='store_true')
    p.add_argument('--settings', type=Path, help='live duration preferences')
    p.add_argument('--seconds', type=float, help='initial duration; 0 means unlimited')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--stop-file', type=Path, help='stop cleanly when this local file is created')
    args = p.parse_args()
    if args.allow_watch:
        ipaddress.IPv4Address(args.allow_watch)
    if args.bind_host:
        ipaddress.IPv4Address(args.bind_host)
    if args.seconds is not None and (not math.isfinite(args.seconds) or args.seconds < 0):
        p.error('duration must be finite and non-negative; 0 means unlimited')
    if args.stop_file is not None and args.stop_file.exists():
        p.error('stop file already exists; use a fresh path')
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print('Stopped.')


if __name__ == '__main__':
    main()
