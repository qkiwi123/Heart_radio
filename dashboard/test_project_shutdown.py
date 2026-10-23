from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from project_shutdown import shutdown_project


class ProjectShutdownTests(unittest.TestCase):
    def test_every_resource_is_released_even_when_a_previous_cleanup_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'runs').mkdir()
            transport=Mock();transport.stop.side_effect=OSError('closed socket')
            access,capability,widgets,server=Mock(),Mock(),Mock(),Mock()
            with patch('project_shutdown.subprocess.run',return_value=Mock(returncode=0)) as receiver:
                errors=shutdown_project(root,transport,access,capability,widgets,server,grace=0)
            access.cancel.assert_called_once();transport.stop.assert_called_once()
            capability.stop.assert_called_once_with(keep_standby=True)
            receiver.assert_called_once();widgets.close.assert_called_once();server.shutdown.assert_called_once()
            self.assertEqual(errors,[dict(resource='connections',error='OSError')])
            self.assertTrue((root/'runs/offline-shutdown-result.json').exists())

if __name__=='__main__':unittest.main()
