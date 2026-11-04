import copy,struct,tempfile,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from server import Store
from game_backend import GameBackend
from shared_saves import SharedSaves
from save_wire import xxhash64,cerimal_checksum


class Peer:
    def __init__(self,actor,account):self.actor=actor;self.identity={'account':account};self.packets=[]
    def send_packet(self,data):self.packets.append(data)


class SharedSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'test.sqlite3','x'*40)
        self.game=GameBackend(self.store,'http://127.0.0.1')
        accounts=[self.store.enroll(n,'x'*40)['account_id'] for n in ('a','b')]
        self.peers=[Peer(i+1,a) for i,a in enumerate(accounts)]
        self.realm=self.game.save_api(accounts[0],'CreateRealm',{'Metadata':'','Contents':''})['Id']
        self.ids=[self.game.save_api(a,'CreateCharacter',{'Metadata':'','Contents':''})['Id'] for a in accounts]
        def doc(b):return b'CERIMAL\x03'+struct.pack('<IIIQ',0,len(b),0,xxhash64(b))+b
        metas=list(map(doc,[b'world-meta',b'a-meta',b'b-meta']))
        contents=list(map(doc,[b'world',b'a-items',b'b-items']))
        blob=lambda b:struct.pack('<i',len(b))+b
        self.raw=blob(metas[0])+blob(contents[0])+struct.pack('<i',2)
        for i,identifier in enumerate(self.ids):self.raw+=uuid.UUID(identifier).bytes_le+blob(metas[i+1])+blob(contents[i+1])
        self.info=struct.pack('<iiiBi',2,2,1,0,2)+struct.pack('<ii',1,2)+b''.join(uuid.UUID(i).bytes_le for i in self.ids)
        self.info+=struct.pack('<6Q',*(cerimal_checksum(b) for b in metas+contents))
        self.info+=struct.pack('<6i',*map(len,metas+contents))
        timeline=SimpleNamespace(players={p.actor:SimpleNamespace(peer=p,active=True) for p in self.peers})
        self.saves=SharedSaves(self.game,self.realm,timeline)

    def tearDown(self):self.temp.cleanup()
    def upload(self,peer=None):
        peer=peer or self.peers[0]
        for n,part in enumerate((self.raw[:40],self.raw[40:])):self.saves.message(peer,10,struct.pack('<iII',2,2,n)+part)

    def test_all_players_must_agree_before_commit_and_ack(self):
        a,b=self.peers;self.saves.message(a,11,self.info);self.upload();self.saves.message(a,12,struct.pack('<i',2))
        self.assertFalse(self.saves.can_leave(1));self.assertEqual([],a.packets)
        with self.store.transaction() as db:self.assertEqual(0,db.execute('SELECT count(*) FROM game_transport_saves').fetchone()[0])
        self.saves.message(b,11,self.info)
        self.assertTrue(self.saves.can_leave(1));self.assertEqual(2,len(a.packets));self.assertEqual(a.packets,b.packets)
        with self.store.transaction() as db:
            self.assertEqual(1,db.execute('SELECT count(*) FROM game_transport_saves').fetchone()[0])
            for identifier in self.ids:self.assertEqual(2,db.execute('SELECT revision FROM game_entities WHERE id=?',(identifier,)).fetchone()[0])

    def test_disagreement_does_not_commit(self):
        self.saves.message(self.peers[0],11,self.info)
        changed=bytearray(self.info);changed[-1]^=1
        with self.assertRaises(ValueError):self.saves.message(self.peers[1],11,bytes(changed))
        self.upload();self.saves.message(self.peers[0],12,struct.pack('<i',2))
        self.assertFalse(self.saves.records[2].committed)

    def test_only_provider_can_upload_chunks(self):
        for p in self.peers:self.saves.message(p,11,self.info)
        with self.assertRaises(ValueError):self.upload(self.peers[1])

    def test_duplicate_finish_is_not_a_second_backend_ack(self):
        for p in self.peers:self.saves.message(p,11,self.info)
        self.upload()
        for _ in range(2):self.saves.message(self.peers[0],12,struct.pack('<i',2))
        self.assertEqual(2,len(self.peers[0].packets))

    def test_departed_character_bundle_is_checked_but_not_overwritten(self):
        from save_wire import cycle_info,chunk,bundle
        for p in self.peers:self.saves.message(p,11,self.info)
        self.upload();self.saves.message(self.peers[0],12,struct.pack('<i',2))
        saved=bundle(self.raw);realm_meta=saved['metadata'];realm_content=saved['contents'];hero=saved['characters'][0]
        # Next cycle is owned by player A after B has left, but the game still
        # includes B's older character in the binary bundle.
        next_info=struct.pack('<iiiBi',3,1,1,0,2)+struct.pack('<i',1)+uuid.UUID(self.ids[0]).bytes_le
        next_info+=struct.pack('<4Q',*map(cerimal_checksum,[realm_meta,hero['metadata'],realm_content,hero['contents']]))
        next_info+=struct.pack('<4i',*map(len,[realm_meta,hero['metadata'],realm_content,hero['contents']]))
        self.saves.timeline.players.pop(2)
        self.saves.message(self.peers[0],11,next_info)
        for n,part in enumerate((self.raw[:40],self.raw[40:])):
            self.saves.message(self.peers[0],10,struct.pack('<iII',3,2,n)+part)
        self.saves.message(self.peers[0],12,struct.pack('<i',3))
        with self.store.transaction() as db:
            self.assertEqual(3,db.execute('SELECT revision FROM game_entities WHERE id=?',(self.ids[0],)).fetchone()[0])
            self.assertEqual(2,db.execute('SELECT revision FROM game_entities WHERE id=?',(self.ids[1],)).fetchone()[0])

if __name__=='__main__':unittest.main(verbosity=2)
