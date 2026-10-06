import json
from pathlib import Path
import tempfile
import unittest
from capability_duration import duration_path, read_duration, save_settings
from data_policy import DataPolicy, DEFAULTS


class DurationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def legacy(self,seconds):
        path=self.root/'runs/data-access/settings.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps(dict(DEFAULTS,pcServiceSeconds=seconds)),encoding='utf-8')

    def test_new_install_defaults_unlimited_and_existing_duration_is_preserved(self):
        self.assertEqual(read_duration(self.root)['seconds'],0)
        self.legacy(600)
        first=read_duration(self.root)
        self.assertEqual(first['seconds'],600)
        self.assertEqual(read_duration(self.root),first)
        self.assertNotIn('pcServiceSeconds',DataPolicy(self.root).read())

    def test_saving_sensor_settings_cannot_erase_legacy_duration(self):
        self.legacy(1200)
        policy=DataPolicy(self.root)
        policy.save(dict(policy.read(),sensorCacheSeconds=60))
        self.assertEqual(read_duration(self.root)['seconds'],1200)
        self.assertNotIn('pcServiceSeconds',json.loads(policy.path.read_text(encoding='utf-8')))

    def test_explicit_duration_settings_take_priority_over_legacy(self):
        self.legacy(1800)
        saved=save_settings(duration_path(self.root),dict(unlimited=True))
        self.assertEqual(read_duration(self.root),saved)

    def test_invalid_legacy_duration_does_not_silently_become_unlimited(self):
        self.legacy(-1)
        with self.assertRaises(OSError):read_duration(self.root)
        self.assertFalse(duration_path(self.root).exists())
        path=self.root/'runs/data-access/settings.json'
        for invalid in ('[]','null','{invalid json'):
            path.write_text(invalid,encoding='utf-8')
            with self.assertRaises(OSError):read_duration(self.root)
            self.assertFalse(duration_path(self.root).exists())
