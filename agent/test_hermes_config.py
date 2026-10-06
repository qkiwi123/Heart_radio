"""Run with the installed Hermes Python after activating its own dependency environment."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('configure_hermes', Path(__file__).parent / 'package' / 'configure_hermes.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
try:
    import hermes_yaml
except ImportError:
    hermes_yaml = None


@unittest.skipIf(hermes_yaml is None, 'Run in the Hermes runtime to validate its actual YAML editor')
class HermesConfigTests(unittest.TestCase):
    def test_preview_add_idempotence_remove_preserve_comments_values_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.yaml'
            original = b'# user note\nmodel: "keep-this-model"\nmcp_servers:\n  other:\n    command: "other-server"\n    env: {EXAMPLE: "off"}\n'
            config.write_bytes(original)
            entry = dict(command='D:/Python/python.exe', args=['D:/AIWorkplace/MCP/heartradio/server.py'])
            preview = module.merge(config, entry, hermes_yaml)
            self.assertFalse(preview['applied'])
            self.assertEqual(config.read_bytes(), original)
            result = module.merge(config, entry, hermes_yaml, apply=True)
            self.assertEqual(Path(result['backup']).read_bytes(), original)
            self.assertIn('# user note', config.read_text())
            self.assertIn('"keep-this-model"', config.read_text())
            saved = config.read_bytes()
            result = module.merge(config, entry, hermes_yaml, apply=True)
            self.assertEqual(result['action'], 'unchanged')
            self.assertEqual(config.read_bytes(), saved)
            module.merge(config, entry, hermes_yaml, action='remove', apply=True)
            self.assertEqual(hermes_yaml.safe_load(config.read_text()), hermes_yaml.safe_load(original))

    def test_empty_null_and_invalid_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.yaml'
            for text in ('', 'mcp_servers: null\n'):
                config.write_text(text)
                module.merge(config, dict(command='python'), hermes_yaml, apply=True)
                self.assertIn('heartradio', hermes_yaml.safe_load(config.read_text())['mcp_servers'])
            for text in ('[]\n', '- invalid\n', 'mcp_servers: [invalid]\n', 'model: a\nmodel: b\n'):
                config.write_text(text)
                with self.assertRaises(Exception):
                    module.merge(config, dict(command='python'), hermes_yaml, apply=True)
                self.assertEqual(config.read_text(), text)


if __name__ == '__main__':
    unittest.main()
