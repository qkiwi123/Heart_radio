"""Synthetic protocol tests only; never count these as watch measurements."""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import shutil
import socket
import ssl
import tempfile
import types
import unittest
from protocol import PACKET
from wifi_receive import run
from control import save_settings


@unittest.skipUnless(Path('.local-wifi/cert.pem').exists(), 'Run wifi_setup first to generate local test certificate')
class ReceiverTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        for name in ('cert.pem', 'key.pem'):
            shutil.copyfile(Path('.local-wifi') / name, self.path / name)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        self.token = bytes(range(32))
        (self.path / 'config.json').write_text(json.dumps(dict(host='127.0.0.1', port=self.port, token=self.token.hex())))
        self.output = self.path / 'synthetic-test.jsonl'
        self.stdout = contextlib.redirect_stdout(io.StringIO())
        self.stdout.__enter__()
        self.stop_file = self.path / 'stop.request'
        self.settings = self.path / 'settings.json'
        self.task = asyncio.create_task(run(types.SimpleNamespace(directory=self.path, output=self.output,
                                               allow_watch='', seconds=0, settings=self.settings, stop_file=self.stop_file)))
        for _ in range(100):
            if self.output.exists() and 'server_start' in self.output.read_text():
                break
            await asyncio.sleep(.01)
        self.context = ssl.create_default_context(cafile=str(self.path / 'cert.pem'))
        self.context.check_hostname = False  # test-only known cert, loopback address
        self.writers = []

    async def connect(self):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.port, ssl=self.context)
        self.writers.append(writer)
        return reader, writer

    async def asyncTearDown(self):
        for writer in self.writers:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(self.task, 2)
        self.stdout.__exit__(None, None, None)
        self.temp.cleanup()

    async def test_fragmented_frame_ack_and_clean_stop(self):
        reader, writer = await self.connect()
        writer.write(self.token); await writer.drain()
        self.assertEqual(await reader.readexactly(1), b'\x01')
        frame = PACKET.pack(1, 2, 123, 0, 0, 0, -128, 6)
        writer.write(frame[:7]); await writer.drain()
        await asyncio.sleep(.02)
        writer.write(frame[7:]); await writer.drain()
        self.assertEqual(await reader.readexactly(1), b'\x01')
        rows = [json.loads(line) for line in self.output.read_text().splitlines()]
        packet = next(r for r in rows if r['kind'] == 'packet')
        self.assertFalse(packet['packet']['valid'])
        self.assertEqual(packet['hex'], frame.hex())
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(self.task, 2)
        self.assertEqual(await reader.read(), b'')

    async def test_wrong_token_never_accepts_packet(self):
        reader, writer = await self.connect()
        writer.write(bytes(32)); await writer.drain()
        self.assertEqual(await reader.read(), b'')
        self.assertNotIn('"kind": "packet"', self.output.read_text())

    async def test_stop_file_closes_authenticated_connection(self):
        reader, writer = await self.connect()
        writer.write(self.token); await writer.drain()
        self.assertEqual(await reader.readexactly(1), b'\x01')
        self.stop_file.touch()
        await asyncio.wait_for(self.task, 2)
        self.assertEqual(await reader.read(), b'')
        self.assertIn('"kind": "server_stop"', self.output.read_text())

    async def test_malformed_frame_closes_without_ack(self):
        reader, writer = await self.connect()
        writer.write(self.token); await writer.drain()
        self.assertEqual(await reader.readexactly(1), b'\x01')
        writer.write(bytes(20)); await writer.drain()
        self.assertEqual(await reader.read(), b'')
        self.assertNotIn('"kind": "packet"', self.output.read_text())

    async def test_live_limit_then_unlimited_then_expiry(self):
        reader, writer = await self.connect()
        writer.write(self.token); await writer.drain()
        self.assertEqual(await reader.readexactly(1), b'\x01')
        save_settings(self.settings, dict(unlimited=False, amount=1/60, unit='minutes'))
        await asyncio.sleep(.4)
        save_settings(self.settings, dict(unlimited=True))
        await asyncio.sleep(1)
        self.assertFalse(self.task.done())
        saved=save_settings(self.settings, dict(unlimited=False, amount=1/60, unit='minutes'))
        await asyncio.wait_for(self.task, 2)
        self.assertEqual(await reader.read(), b'')
        rows=[json.loads(line) for line in self.output.read_text().splitlines()]
        applied=[r for r in rows if r['kind']=='receiver_limit'][-1]
        self.assertEqual(applied['endsMs'], saved['savedMs']+1000)
        self.assertIn('capture_window_complete', [r['kind'] for r in rows])
