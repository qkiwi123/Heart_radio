import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from export_package import export

spec = importlib.util.spec_from_file_location('package_check', Path(__file__).parent / 'package' / 'check.py')
check_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_module)


class PackageTests(unittest.TestCase):
    def test_independent_package_handshake_offline_and_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'heartradio'
            result = export(destination, Path(sys.executable), port=1, allow_dirty=True)
            self.assertFalse(result['agentConfigModified'])
            status = check_module.check(destination)
            self.assertTrue(status['packageValid'])
            self.assertEqual(len(status['tools']), 6)
            self.assertEqual(status['localService'], 'local_service_unavailable')
            self.assertFalse(status['localServiceAvailable'])
            (destination / 'tools.json').write_text('[]', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'changed or missing'):
                check_module.check(destination)

    def test_upgrade_keeps_unmanaged_files_and_single_index_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'heartradio'
            export(destination, Path(sys.executable), allow_dirty=True)
            manifest = (destination / 'VERSION.json').read_bytes()
            notes = destination / 'personal-notes.md'
            notes.write_text('keep me', encoding='utf-8')
            export(destination, Path(sys.executable), port=9999, allow_dirty=True)
            self.assertEqual(notes.read_text(), 'keep me')
            backups = list((destination / 'backups').glob('*/VERSION.json'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), manifest)
            self.assertEqual((destination.parent / 'README.md').read_text(encoding='utf-8').count('<!-- heartradio:start -->'), 1)
            config = json.loads((destination / 'clients/hermes.json').read_text())
            self.assertEqual(config['mcp_servers']['heartradio']['args'][-1], '9999')

    def test_refuses_nonmanaged_destination_and_unsafe_old_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'heartradio'
            destination.mkdir()
            (destination / 'notes').write_text('not a package')
            with self.assertRaisesRegex(ValueError, 'not a managed'):
                export(destination, Path(sys.executable), allow_dirty=True)
            (destination / 'VERSION.json').write_text(json.dumps(dict(package='heartradio', files={'../secret': 'x'})))
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                export(destination, Path(sys.executable), allow_dirty=True)


if __name__ == '__main__':
    unittest.main()
