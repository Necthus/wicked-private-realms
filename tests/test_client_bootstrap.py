"""Exercise the real CLI entry points without Python's script-directory search."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest


class ClientBootstrapTests(unittest.TestCase):
    def test_entry_points_import_in_isolated_python_from_unrelated_directory(self):
        root=Path(__file__).resolve().parents[1]/'archive/python-client'
        with tempfile.TemporaryDirectory(prefix='wicked isolated directory ') as cwd:
            for filename in ('connect_and_launch.py','install_gse.py','launch.py'):
                with self.subTest(entry=filename):
                    result=subprocess.run([sys.executable,'-I',str(root/'client'/filename),'--help'],
                        cwd=cwd,capture_output=True,text=True,encoding='utf-8',timeout=15,
                        env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')})
                    self.assertEqual(0,result.returncode,result.stderr)
                    self.assertIn('usage:',result.stdout.lower())
                    self.assertNotIn('ModuleNotFoundError',result.stderr)
