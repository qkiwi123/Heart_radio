import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from control import Deadline, read_settings, save_settings
from discovery import discovery_reply, signature
from lan import LanAdapter, private_ipv4


class SettingsTest(unittest.TestCase):
    def test_default_unlimited_and_units(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            self.assertEqual(read_settings(path)['seconds'], 0)
            result = save_settings(path, dict(unlimited=False, amount=1.5, unit='hours'))
            self.assertEqual(result['seconds'], 5400)
            self.assertEqual(read_settings(path), result)
            self.assertEqual(save_settings(path, dict(unlimited=True))['seconds'], 0)

    def test_invalid_does_not_replace_saved_setting(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            saved = save_settings(path, dict(unlimited=True))
            for amount in (0, -1, True, float('nan'), float('inf'), '60'):
                with self.assertRaises(ValueError):
                    save_settings(path, dict(unlimited=False, amount=amount, unit='minutes'))
                self.assertEqual(read_settings(path), saved)

    def test_live_change_counts_from_save_not_original_start(self):
        with patch('control.time.monotonic', return_value=100):
            deadline = Deadline(30)
        with patch('control.time.monotonic', return_value=140):
            self.assertTrue(deadline.expired())
            settings = dict(seconds=60, revision='new', savedMonotonic=139, savedMs=2000)
            self.assertTrue(deadline.apply(settings))
            self.assertEqual(deadline.ends, 199)
            self.assertFalse(deadline.expired())
            self.assertFalse(deadline.apply(settings))
            self.assertTrue(deadline.apply(dict(settings, seconds=0, revision='unlimited')))
            self.assertIsNone(deadline.ends)

    def test_restart_uses_full_duration_not_previous_deadline(self):
        settings = dict(seconds=60, revision='saved', savedMonotonic=10, savedMs=1)
        with patch('control.time.monotonic', return_value=500):
            deadline = Deadline(settings['seconds'], settings)
            self.assertEqual(deadline.ends, 560)
            self.assertFalse(deadline.apply(settings))


class DiscoveryTest(unittest.TestCase):
    def test_authenticated_nonce_and_no_plain_secret(self):
        token = bytes(range(32)); pin = 'a'*64; nonce = 'b'*32
        body = f'HRDISC1|{nonce}|{pin}'
        request = (body+'|'+signature(token, body)).encode()
        response = discovery_reply(request, token, pin, 8765)
        self.assertEqual(response.decode(), f'HRHERE1|{nonce}|{pin}|8765|'+signature(token, f'HRHERE1|{nonce}|{pin}|8765'))
        self.assertNotIn(token.hex().encode(), request)
        self.assertIsNone(discovery_reply(request[:-1]+b'z', token, pin, 8765))
        self.assertIsNone(discovery_reply(request, token, 'c'*64, 8765))
        self.assertIsNone(discovery_reply(b'garbage', token, pin, 8765))

    def test_adapter_stays_selected_when_ip_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            first = dict(name='wifi', type=71, ips=['192.168.1.8'], gateway='192.168.1.1')
            with patch('lan.adapters', return_value=[first]):
                adapter = LanAdapter(Path(folder), '192.168.1.8')
                self.assertEqual(adapter.address(), '192.168.1.8')
            with patch('lan.adapters', return_value=[dict(first, ips=['10.0.0.8'])]):
                self.assertEqual(adapter.address(), '10.0.0.8')
            with patch('lan.adapters', return_value=[dict(first, name='other')]):
                self.assertIsNone(adapter.address())
        for ip in ('0.0.0.0','8.8.8.8','169.254.1.2','127.0.0.1'):
            self.assertFalse(private_ipv4(ip))
