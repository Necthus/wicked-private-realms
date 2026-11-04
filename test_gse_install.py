import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from client.install_gse import install_gse
from client.steam_identity import config_path,configured_identity


class GseInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.game=root/'game';self.game.mkdir();self.client=self.game/'wicked-client'
        self.bundle=self.client/'plugin/gse';(self.bundle/'steam_settings').mkdir(parents=True)
        (self.game/'GameAssembly.dll').write_bytes(b'test-supported-build')
        (self.client/'research').mkdir()
        (self.client/'research/client-fingerprint.json').write_text(json.dumps({'GameAssembly.dll':{'sha256':hashlib.sha256(b'test-supported-build').hexdigest()}}))
        (self.bundle/'steam_api64.dll').write_bytes(b'test-approved-gse')
        (self.bundle/'steam_settings/configs.user.ini').write_text('[user::general]\naccount_name=PrivateRealmPlayer\naccount_steamid=0\n')
        (self.bundle/'steam_settings/configs.main.ini').write_text('[main::connectivity]\noffline=0\n')
        digest=hashlib.sha256(b'test-approved-gse').hexdigest()
        files={p.relative_to(self.bundle).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in self.bundle.rglob('*') if p.is_file()}
        (self.bundle/'manifest.json').write_text(json.dumps({'dll_sha256':digest,'files':files}))
        self.pin=patch('client.install_gse.PINNED_GSE_SHA256',digest);self.pin.start()
        self.target=self.game/'NoRestForTheWicked_Data/Plugins/x86_64';self.target.mkdir(parents=True)
        (self.target/'steam_api64.dll').write_bytes(b'previous-emulator')
    def tearDown(self):self.pin.stop();self.tmp.cleanup()

    def test_replacement_is_exact_and_original_recoverable(self):
        result=install_gse(self.game,self.client,'76561197960278073')
        self.assertTrue(result['installed'])
        self.assertEqual(b'previous-emulator',(Path(result['backup'])/'steam_api64.dll').read_bytes())
        self.assertEqual(b'test-approved-gse',(self.target/'steam_api64.dll').read_bytes())
        self.assertEqual('76561197960278073',configured_identity(self.game))

    def test_known_gse_preserves_existing_personal_configuration(self):
        install_gse(self.game,self.client,'76561197960278073')
        config=config_path(self.game)
        config.write_text(config.read_text().replace('PrivateRealmPlayer','MyName')+'language=schinese\n')
        result=install_gse(self.game,self.client,'76561197960278074')
        self.assertFalse(result['installed']);self.assertIn('account_name=MyName',config.read_text())
        self.assertEqual('76561197960278074',configured_identity(self.game))

    def test_corrupt_bundle_and_wrong_build_do_not_change_game(self):
        (self.bundle/'steam_api64.dll').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'approved build'):install_gse(self.game,self.client,'76561197960278073')
        self.assertEqual(b'previous-emulator',(self.target/'steam_api64.dll').read_bytes())
        (self.bundle/'steam_api64.dll').write_bytes(b'test-approved-gse')
        (self.game/'GameAssembly.dll').write_bytes(b'unsupported')
        with self.assertRaisesRegex(ValueError,'Unsupported game'):install_gse(self.game,self.client,'76561197960278073')
        self.assertEqual(b'previous-emulator',(self.target/'steam_api64.dll').read_bytes())
