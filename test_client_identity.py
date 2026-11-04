import unittest
from pathlib import Path
from client.client_identity import cache_directory,game_arguments


class ClientIdentityTests(unittest.TestCase):
    def profile(self,account='11111111-2222-3333-4444-555555555555',server='server-a'):
        return {'account_id':account,'server_id':server,'endpoint':'http://127.0.0.1:18770/game','name':'necthus'}

    def test_distinct_accounts_and_servers_have_distinct_caches(self):
        root=Path('test-root')
        a=cache_directory(root,self.profile())
        b=cache_directory(root,self.profile('66666666-2222-3333-4444-555555555555'))
        c=cache_directory(root,self.profile(server='server-b'))
        self.assertEqual(3,len({a,b,c}))
        self.assertEqual('DataStore',a.name)
        self.assertTrue(a.is_relative_to(root/'data/account-cache-v2'))

    def test_display_name_change_does_not_change_cache_identity(self):
        a=self.profile();b=dict(a,name='oldust')
        self.assertEqual(cache_directory('.',a),cache_directory('.',b))

    def test_invalid_account_id_cannot_escape_cache(self):
        with self.assertRaises(ValueError):cache_directory('.',self.profile('../other'))

    def test_game_uses_saved_graphics_preferences(self):
        args=game_arguments('game.exe','Player.log')
        self.assertEqual(['game.exe','-logFile','Player.log'],args)
        self.assertFalse(any(arg.startswith(('-screen','-force','-quality')) for arg in args))

    def test_steam_identity_uses_original_game_save_path(self):
        profile=dict(self.profile(),steam_id='76561197960278073')
        self.assertIsNone(cache_directory('test-root',profile))
