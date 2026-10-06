import json
from pathlib import Path
import tempfile
import unittest
from process_registry import register, unregister


class RegistryTests(unittest.TestCase):
    def project(self, folder):
        root=Path(folder); (root/'scripts').mkdir(); (root/'scripts/Start-HeartRadio.ps1').touch()
        entry=root/'agent/mcp_server.py'; entry.parent.mkdir(); entry.write_text('# adapter')
        return root,entry

    def test_adapter_record_removed_on_own_clean_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            root,entry=self.project(folder)
            registration=register(entry,dict(pid=12,creationTicks='345',executable=str(root/'python.exe')))
            path,value=registration
            self.assertEqual(path.name,'12-345.json')
            self.assertEqual(json.loads(path.read_text())['component'],'mcp_adapter')
            self.assertNotIn('arguments',value)
            unregister(registration)
            self.assertFalse(path.exists())

    def test_exit_does_not_remove_replaced_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root,entry=self.project(folder)
            registration=register(entry,dict(pid=12,creationTicks='345',executable=str(root/'python.exe')))
            path,value=registration; path.write_text(json.dumps(dict(value,creationTicks='999')))
            unregister(registration)
            self.assertTrue(path.exists())

    def test_standalone_package_uses_declared_project(self):
        with tempfile.TemporaryDirectory() as folder:
            root,entry=self.project(folder)
            deployed=root/'deployed'; deployed.mkdir(); server=deployed/'server.py'; server.write_text('# adapter')
            (deployed/'runtime.json').write_text(json.dumps(dict(sourceProject=str(root))))
            registration=register(server,dict(pid=13,creationTicks='346',executable=str(root/'python.exe')))
            self.assertEqual(registration[0].parent,root/'runs/mcp-adapters')
            unregister(registration)


if __name__=='__main__': unittest.main()
