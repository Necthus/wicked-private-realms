import base64
import tempfile
import unittest
from pathlib import Path
from server import ApiError, Store
from game_backend import GameBackend


class GameBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'test.db','x'*40)
        self.api=GameBackend(self.store,'http://127.0.0.1:18768')
        self.a=self.store.enroll('Alice','x'*40)
        self.b=self.store.enroll('Bob','x'*40)
        self.save={'Metadata':base64.b64encode(b'metadata').decode(),
                   'Contents':base64.b64encode(b'world').decode()}

    def tearDown(self): self.tmp.cleanup()

    def call(self,action,payload=None,account=None):
        return self.api.save_api((account or self.a)['account_id'],action,payload or {})

    def test_login_requires_private_account_token(self):
        with self.assertRaises(ApiError):
            self.api.request('/game/auth/v3/token','POST',{'entitlementTicket':'not-private-token'},'')
        result=self.api.request('/game/auth/v3/token','POST',{'entitlementTicket':self.a['token']},'')
        self.assertEqual(self.a['account_id'],self.api.verify('Bearer '+result['data'][0]['token']))

    def test_tampered_session_rejected(self):
        token=self.api.token(self.a['account_id'])
        with self.assertRaises(ApiError): self.api.verify(token[:-5]+'xxxxx')

    def test_path_account_impersonation_rejected(self):
        with self.assertRaises(ApiError):
            self.api.request('/game/rest/save/'+self.b['account_id']+'/ListCharacters','POST',{},
                             'Bearer '+self.api.token(self.a['account_id']))

    def test_realm_auto_shared_and_bytes_preserved(self):
        created=self.call('CreateRealm',self.save)
        self.assertEqual(0,created['Status'])
        rows=self.call('ListRealms',account=self.b)
        self.assertEqual(created['Id'],rows[0]['RealmId'])
        self.assertEqual(1,rows[0]['Participation']['OwnKind'])
        loaded=self.call('LoadRealm',{'RealmId':created['Id']},self.b)
        self.assertEqual(self.save['Contents'],loaded['Contents'])

    def test_characters_private(self):
        char=self.call('CreateCharacter',self.save)
        self.assertEqual([],self.call('ListCharacters',account=self.b))
        with self.assertRaises(ApiError): self.call('LoadCharacter',{'CharacterId':char['Id']},self.b)

    def test_group_cannot_overwrite_owner_realm(self):
        realm=self.call('CreateRealm',self.save)
        with self.assertRaises(ApiError):
            self.call('SaveData',{'Realm':dict(self.save,Id=realm['Id']),'Characters':[]},self.b)

    def test_atomic_save_rolls_back_on_invalid_character(self):
        realm=self.call('CreateRealm',self.save)
        changed=dict(self.save,Id=realm['Id'],Contents=base64.b64encode(b'changed').decode())
        with self.assertRaises(ApiError):
            self.call('SaveData',{'Realm':changed,'Characters':[dict(self.save,Id='missing')]})
        self.assertEqual(self.save['Contents'],self.call('LoadRealm',{'RealmId':realm['Id']})['Contents'])

    def test_history_persisted(self):
        realm=self.call('CreateRealm',self.save)
        self.call('SaveData',{'Realm':dict(self.save,Id=realm['Id']),'Characters':[]})
        with self.store.transaction() as db:
            self.assertEqual(2,db.execute('SELECT count(*) FROM game_entity_versions WHERE id=?',(realm['Id'],)).fetchone()[0])

    def test_realtime_not_faked(self):
        realm=self.call('CreateRealm',self.save)
        response=self.call('PlayRealm',{'RealmId':realm['Id']})
        self.assertEqual(1001,response['Status'])
        self.assertIsNone(response['PlayJwt'])
        self.assertEqual({'Status','Region','PlayJwt'},set(response))

    def test_transport_save_is_atomic_and_idempotent(self):
        realm=self.call('CreateRealm',self.save)['Id']
        char=self.call('CreateCharacter',self.save)['Id']
        save={'metadata':b'realm2','contents':b'world2','characters':[
            {'id':char,'metadata':b'hero2','contents':b'items2'}]}
        ticks=self.api.commit_transport_save(self.a['account_id'],realm,'test-session',2,b'raw',save)
        self.assertEqual(ticks,self.api.commit_transport_save(self.a['account_id'],realm,'test-session',2,b'raw',save))
        with self.store.transaction() as db:
            self.assertEqual(2,db.execute('SELECT revision FROM game_entities WHERE id=?',(realm,)).fetchone()[0])
            self.assertEqual(1,db.execute('SELECT count(*) FROM game_transport_saves').fetchone()[0])
        with self.assertRaises(ApiError):self.api.commit_transport_save(self.a['account_id'],realm,'test-session',2,b'changed',save)
        bad=dict(save,characters=[{'id':'missing','metadata':b'x','contents':b'y'}])
        with self.assertRaises(ApiError):self.api.commit_transport_save(self.a['account_id'],realm,'test-session',3,b'new',bad)
        with self.store.transaction() as db:
            self.assertEqual(2,db.execute('SELECT revision FROM game_entities WHERE id=?',(realm,)).fetchone()[0])

    def test_transport_save_does_not_modify_other_players_character(self):
        realm=self.call('CreateRealm',self.save)['Id']
        char=self.call('CreateCharacter',self.save,account=self.b)['Id']
        with self.assertRaises(ApiError):
            self.api.commit_transport_save(self.a['account_id'],realm,'s',2,b'raw',{
                'metadata':b'x','contents':b'y','characters':[{'id':char,'metadata':b'x','contents':b'y'}]})

    def test_shared_realm_cannot_be_overwritten_by_legacy_offline_save(self):
        shared=GameBackend(self.store,'http://127.0.0.1:18770',transport_experiment=True)
        realm=self.call('CreateRealm',self.save)['Id']
        with self.assertRaises(ApiError) as error:
            shared.save_api(self.a['account_id'],'SaveData',{'Realm':dict(self.save,Id=realm),'Characters':[]})
        self.assertEqual(409,error.exception.status)
        self.assertEqual(self.save['Contents'],self.call('LoadRealm',{'RealmId':realm})['Contents'])
        with self.store.transaction() as db:
            self.assertEqual(1,db.execute('SELECT revision FROM game_entities WHERE id=?',(realm,)).fetchone()[0])


if __name__=='__main__':unittest.main(verbosity=2)
