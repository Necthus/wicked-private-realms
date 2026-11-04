import tempfile
import unittest
from pathlib import Path
from client.steam_identity import bind_config,config_path,configured_identity


class SteamConfigTests(unittest.TestCase):
    def test_binding_preserves_other_settings_and_backups_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            game=Path(tmp);path=config_path(game);path.parent.mkdir(parents=True)
            original=b'\xef\xbb\xbf[user::general]\r\n# identity\r\naccount_name=Necthus\r\naccount_steamid=76561197960278073\r\nlanguage=schinese\r\n[user::saves]\r\nlocal_save_path=C:\\Saved\r\n'
            path.write_bytes(original)
            self.assertEqual('76561197960278073',configured_identity(game))
            self.assertTrue(bind_config(game,'76561197960278074',game/'backups'))
            changed=path.read_bytes()
            self.assertEqual(original.replace(b'76561197960278073',b'76561197960278074'),changed)
            self.assertEqual(original,next((game/'backups').iterdir()).read_bytes())
            self.assertEqual('76561197960278074',configured_identity(game))
            self.assertTrue(bind_config(game,'76561197960278074',game/'backups'))
            self.assertEqual(1,len(list((game/'backups').iterdir())))

    def test_genuine_install_without_emulator_config_is_not_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(bind_config(tmp,'76561197960278073',Path(tmp)/'backups'))
            self.assertEqual([],list(Path(tmp).iterdir()))

    def test_invalid_id_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp,self.assertRaises(ValueError):bind_config(tmp,'necthus',Path(tmp)/'backups')
