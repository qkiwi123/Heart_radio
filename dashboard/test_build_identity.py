from pathlib import Path
import tempfile
import unittest

from build_identity import runtime_sources, server_build


class BuildIdentityTests(unittest.TestCase):
    def test_all_previously_omitted_runtime_modules_invalidate_the_identity(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder)
            for name in runtime_sources(root):
                target = copy / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((root / name).read_bytes())
            baseline = server_build(copy)
            for name in ('dashboard/widgets.py', 'dashboard/history.py', 'dashboard/state.py',
                         'scripts/data_policy.py', 'scripts/capability_service.py'):
                target = copy / name
                before = target.read_bytes()
                target.write_bytes(before + b'\n# source updated\n')
                self.assertNotEqual(server_build(copy), baseline, name)
                target.write_bytes(before)
            # Editing tests and user data must not make daily startup replace a service.
            (copy / 'dashboard/test_ignored.py').write_text('test only')
            (copy / 'runs').mkdir()
            (copy / 'runs/settings.json').write_text('{}')
            self.assertEqual(server_build(copy), baseline)


if __name__ == '__main__':
    unittest.main()
