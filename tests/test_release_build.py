import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from native import build_native_release as client
from tools import fetch_ci_gse


class ReleaseBuildTests(unittest.TestCase):
    def test_pinned_download_rejects_changed_bytes_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'upstream.zip'
            output.write_bytes(b'previous verified bundle')
            with mock.patch.object(fetch_ci_gse.urllib.request, 'urlopen', return_value=io.BytesIO(b'changed download')):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    fetch_ci_gse.fetch(output)
            self.assertEqual(b'previous verified bundle', output.read_bytes())

    def test_pinned_download_writes_only_verified_bytes(self):
        raw = b'verified download fixture'
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'new directory' / 'upstream.zip'
            with mock.patch.object(fetch_ci_gse, 'SOURCE_SHA256', hashlib.sha256(raw).hexdigest()), \
                    mock.patch.object(fetch_ci_gse.urllib.request, 'urlopen', return_value=io.BytesIO(raw)), \
                    contextlib.redirect_stdout(io.StringIO()):
                fetch_ci_gse.fetch(output)
            self.assertEqual(raw, output.read_bytes())

    def test_client_package_records_version_revision_and_only_approved_gse(self):
        gse = b'approved GSE fixture'
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source = directory / 'tested.zip'
            binaries = {name: ('compiled fixture: ' + name).encode() for name in client.BINARIES}
            metadata = {'mode': 'native', 'game_build': '29466',
                        'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in binaries.items()}}
            with zipfile.ZipFile(source, 'w') as bundle:
                for name, raw in binaries.items():
                    bundle.writestr('wicked-client/' + name, raw)
                bundle.writestr('wicked-client/native/BUILD-METADATA.json', json.dumps(metadata))
                bundle.writestr('wicked-client/plugin/gse/steam_api64.dll', gse)
            output = directory / 'output'
            arguments = ['build_native_release.py', '--binaries-from', str(source), '--output', str(output),
                         '--version', 'v0.2.6-rc.1', '--revision', 'a' * 40]
            with mock.patch.object(sys, 'argv', arguments), \
                    mock.patch.object(client, 'GSE_HASH', hashlib.sha256(gse).hexdigest()), \
                    contextlib.redirect_stdout(io.StringIO()):
                client.main()
            package = output / 'Wicked-Private-Realms-Client-Native-v29466-v0.2.6-rc.1.zip'
            with zipfile.ZipFile(package) as bundle:
                built = json.loads(bundle.read('wicked-client/native/BUILD-METADATA.json'))
                self.assertEqual('v0.2.6-rc.1', built['version'])
                self.assertEqual('a' * 40, built['source_revision'])
                self.assertEqual(metadata['files'], built['files'])
                self.assertEqual(gse, bundle.read('wicked-client/plugin/gse/steam_api64.dll'))
                self.assertIn('wicked-client/CLIENT.md', bundle.namelist())
                self.assertFalse(any(name.endswith(('.py', '.js', '.pem', '.key', '.sqlite3')) for name in bundle.namelist()))
            self.assertEqual(hashlib.sha256(package.read_bytes()).hexdigest(),
                             (output / 'SHA256SUMS.txt').read_text().split()[0])
            with mock.patch.object(sys, 'argv', arguments), mock.patch.object(client, 'GSE_HASH', '0' * 64):
                with self.assertRaisesRegex(ValueError, 'Wrong GSE DLL'):
                    client.main()

    def test_invalid_package_identifiers_fail_before_packaging(self):
        for option, value in [('--version', '../escape'), ('--version', 'v0.2.6/escape'),
                              ('--version', 'not-a-version'), ('--revision', 'not-a-commit')]:
            with self.subTest(option=option, value=value), \
                    mock.patch.object(sys, 'argv', ['build_native_release.py', option, value]), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    client.main()
                self.assertEqual(2, error.exception.code)


if __name__ == '__main__':
    unittest.main()
