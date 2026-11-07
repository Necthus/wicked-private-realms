import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.probe_standalone_runtime import main


class StandaloneRuntimeProbeTests(unittest.TestCase):
    def test_incomplete_runner_request_never_spawns_child(self):
        with patch('tools.probe_standalone_runtime.subprocess.run') as spawn:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(['--runner-probe'])
            self.assertEqual(error.exception.code, 2)
            spawn.assert_not_called()

    def test_direct_child_also_rejects_unknown_game_build(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'GameAssembly.dll').write_bytes(b'unsupported')
            with patch('tools.probe_standalone_runtime.sys.platform', 'win32'):
                with patch('tools.probe_standalone_runtime.child') as load:
                    with self.assertRaisesRegex(ValueError, 'Unsupported game build'):
                        main(['--child', '--game-directory', directory, '--skip-imgui-eager-init'])
                    load.assert_not_called()
