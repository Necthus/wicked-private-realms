import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from game_backend import GameBackend
from launcher_auth import password_hash
from server import ApiError,Store


class LauncherLoginTests(unittest.TestCase):
    a='76561197960278073'
    b='76561197960278074'
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'test.db','x'*40)
        self.api=GameBackend(self.store,'http://127.0.0.1:18770',True,password_hash('fixture-password-only'))
    def tearDown(self):self.tmp.cleanup()
    def login(self,identifier=None,password='fixture-password-only'):
        return self.api.request('/game/launcher/login','POST',{'steam_id':identifier or self.a,'password':password},'')

    def test_wrong_password_does_not_create_account(self):
        with self.assertRaises(ApiError) as err:self.login(password='wrong')
        self.assertEqual(403,err.exception.status)
        with self.store.transaction() as db:self.assertEqual(0,db.execute('SELECT count(*) FROM accounts').fetchone()[0])

    def test_existing_steam_mapping_preserves_internal_guid_and_saves(self):
        old=self.store.enroll('necthus','x'*40)
        with self.store.transaction() as db:
            db.execute('UPDATE accounts SET name=? WHERE id=?',(self.a,old['account_id']))
            db.execute('INSERT INTO game_steam_accounts VALUES(?,?)',(self.a,old['account_id']))
        result=self.login()
        self.assertFalse(result['created']);self.assertEqual(old['account_id'],result['account_id'])
        self.assertEqual(self.a,result['name']);self.assertEqual(self.a,result['steam_id'])

    def test_new_steam_id_created_and_game_ticket_accepted(self):
        result=self.login(self.b)
        self.assertTrue(result['created'])
        token=self.api.request('/game/auth/v3/token','POST',{'entitlementTicket':result['token']},'')
        self.assertEqual(result['account_id'],token['data'][0]['accountId'])
        self.assertEqual(result['account_id'],self.login(self.b)['account_id'])

    def test_names_and_numeric_json_ids_rejected(self):
        for value in ('necthus','oldust','123',' 76561197960278073',76561197960278073,'76561197960265728','７６５６１１９８９０３２４２１５７'):
            with self.subTest(value=value),self.assertRaises(ApiError):self.login(value)
        with self.assertRaises(ApiError):
            self.api.request('/game/launcher/login','POST',{'name':'necthus','password':'fixture-password-only'},'')

    def test_concurrent_same_steam_id_has_one_identity(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            rows=list(pool.map(lambda _:self.login(),range(4)))
        self.assertEqual(1,len({row['account_id'] for row in rows}))
        self.assertEqual(1,sum(row['created'] for row in rows))

    def test_different_steam_ids_keep_characters_separate(self):
        a=self.login();b=self.login(self.b)
        char=self.api.save_api(a['account_id'],'CreateCharacter',{'Metadata':'','Contents':''})['Id']
        self.assertEqual([],self.api.save_api(b['account_id'],'ListCharacters',{}))
        self.assertEqual(char,self.api.save_api(a['account_id'],'ListCharacters',{})[0]['CharacterId'])

    def test_login_disabled_without_password_configuration(self):
        api=GameBackend(self.store,'http://127.0.0.1:18770')
        with self.assertRaises(ApiError) as err:api.request('/game/launcher/login','POST',{'steam_id':self.a,'password':'fixture-password-only'},'')
        self.assertEqual(503,err.exception.status)
