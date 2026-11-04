import socket,struct,threading,unittest,tempfile
import json
from types import SimpleNamespace
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch
from photon_wire import KeyExchange,Reader,frame,parameters,Byte
from tools.photon_tcp_probe import Server,Handler
from server import Store
from game_backend import GameBackend
from quantum_wire import pack,Bits
from save_wire import bundle

class TransportTests(unittest.TestCase):
    def setUp(self):
        # Synthetic connections must not be mixed into live client evidence.
        self.log_patch=patch('tools.photon_tcp_probe.emit')
        self.log_patch.start();self.addCleanup(self.log_patch.stop)
    def test_private_auth_and_master_assignment(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'game.sqlite3','x'*40)
            game=GameBackend(store,'http://127.0.0.1',True)
            account=store.enroll('Test','x'*40)['account_id']
            realm=game.save_api(account,'CreateRealm',{'Metadata':'','Contents':''})['Id']
            replies=[]
            class FakeSocket:
                def sendall(self,data):replies.append(data)
            class State:pass
            state=State();state.game=game;state.tickets={}
            handler=Handler.__new__(Handler);handler.server=state;handler.request=FakeSocket()
            handler.identity=None;handler.ticket=None;handler.role='master'
            self.assertTrue(handler.operation(230,{216:urlencode({'PlayJwt':game.token(account,realm)}),225:account}))
            self.assertEqual(account,handler.identity['account'])
            self.assertEqual('Test',handler.identity['name'])
            self.assertTrue(handler.operation(226,{255:realm}))
            self.assertIn(b'127.0.0.1:18769',replies[-1])
            other=Handler.__new__(Handler);other.server=state;other.request=FakeSocket()
            other.identity=None;other.ticket=None;other.role='master'
            self.assertTrue(other.operation(230,{221:handler.ticket}))
            self.assertEqual('game',other.role)
            self.assertTrue(other.operation(226,{255:realm}))
            self.assertIn(b'Quantum room simulation',replies[-1])

    def test_room_membership_limit_and_cleanup(self):
        class FakeSocket:
            def __init__(self):self.replies=[]
            def sendall(self,data):self.replies.append(data)
        server=Server(('127.0.0.1',0),Handler,allow_room_probe=True)
        handlers=[]
        try:
            for i in range(5):
                h=Handler.__new__(Handler);h.server=server;h.request=FakeSocket()
                h.identity={'account':str(i),'realm':'test-realm'};h.role='game';h.room=None
                handlers.append(h)
                self.assertTrue(h.operation(226,{255:'test-realm',249:{Byte(255):'Player '+str(i)}}))
            self.assertEqual(4,len(server.rooms['test-realm']['players']))
            self.assertIsNone(handlers[4].room)
            handlers[0].leave_room()
            handlers[4].operation(226,{255:'test-realm'})
            self.assertEqual(4,len(server.rooms['test-realm']['players']))
            self.assertEqual(5,handlers[4].actor)
            for h in handlers:h.leave_room()
            self.assertEqual({},server.rooms)
        finally:server.server_close()

    def test_client_rtt_is_consumed_without_echo(self):
        class FakeSocket:
            def __init__(self):self.replies=[]
            def sendall(self,data):self.replies.append(data)
        class State:game=None
        h=Handler.__new__(Handler);h.server=State();h.request=FakeSocket()
        h.identity={'account':'synthetic','realm':'synthetic'};h.room={'players':{}}
        raw,_=pack('RttUpdate',value=25)
        with patch('pathlib.Path.write_bytes'):
            self.assertTrue(h.operation(253,{244:101,245:raw}))
        self.assertEqual(25,h.max_ping)
        self.assertEqual([],h.request.replies)

    def test_account_name_overrides_local_nickname_and_other_player_edit_rejected(self):
        class FakeSocket:
            def __init__(self):self.replies=[]
            def sendall(self,data):self.replies.append(data)
        server=Server(('127.0.0.1',0),Handler,allow_room_probe=True)
        try:
            peers=[]
            for name in ('necthus','oldust'):
                h=Handler.__new__(Handler);h.server=server;h.request=FakeSocket()
                h.identity={'account':name,'realm':'test-realm','name':name};h.role='game';h.room=None
                h.operation(226,{255:'test-realm',249:{Byte(255):'Necthus'}})
                self.assertEqual(name,h.player_properties[Byte(255)])
                peers.append(h)
            peers[1].operation(252,{254:peers[1].actor,251:{Byte(255):'Necthus'}})
            self.assertEqual('oldust',peers[1].player_properties[Byte(255)])
            peers[1].operation(252,{254:peers[0].actor,251:{Byte(255):'wrong'}})
            self.assertEqual('necthus',peers[0].player_properties[Byte(255)])
        finally:server.server_close()

    @unittest.skipUnless((Path(__file__).parent/'research/save-bundle-reference.bin').is_file(),'Optional private native save fixture is not distributed')
    def test_real_save_bundle_commits_before_success_ack(self):
        root=Path(__file__).parent/'research'
        raw=(root/'save-bundle-reference.bin').read_bytes()
        native=json.loads((root/'save-native-vectors.json').read_text())
        decoded=bundle(raw)
        class FakeSocket:
            def __init__(self):self.replies=[]
            def sendall(self,data):self.replies.append(data)
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'game.sqlite3','x'*40)
            game=GameBackend(store,'http://127.0.0.1',True)
            account=store.enroll('Test','x'*40)['account_id']
            realm=game.save_api(account,'CreateRealm',{'Metadata':'','Contents':''})['Id']
            with store.transaction() as db:
                c=decoded['characters'][0]
                db.execute('INSERT INTO game_entities VALUES(?,?,?,?,?,?,?)',(c['id'],'character',account,b'old',b'old',1,0))
            server=Server(('127.0.0.1',0),Handler,game=game)
            try:
                h=Handler.__new__(Handler);h.server=server;h.request=FakeSocket();h.actor=1
                h.identity={'account':account,'realm':realm};h.ticket='test-session';h.room={'players':{1:h}};h.save_cycles={}
                h.save_message(11,bytes.fromhex(native['infoHex']))
                h.save_message(12,struct.pack('<i',2))
                missing=Reader(h.request.replies[-1][10:]).params()[0]
                self.assertEqual(1,missing[4])
                for index in range(3):
                    h.save_message(10,struct.pack('<iII',2,3,index)+raw[index*32768:(index+1)*32768])
                h.save_message(12,struct.pack('<i',2))
                success_params=Reader(h.request.replies[-2][10:]).params()
                backend_params=Reader(h.request.replies[-1][10:]).params()
                self.assertNotIn(245,success_params);self.assertNotIn(245,backend_params)
                success=success_params[0];backend=backend_params[0]
                self.assertEqual(0,success[4]);self.assertEqual(1,backend[4])
                with store.transaction() as db:
                    r=db.execute('SELECT contents,revision FROM game_entities WHERE id=?',(realm,)).fetchone()
                    self.assertEqual(decoded['contents'],r['contents']);self.assertEqual(2,r['revision'])
                    self.assertEqual(1,db.execute('SELECT count(*) FROM game_transport_saves').fetchone()[0])
                h.save_message(12,struct.pack('<i',2))
                with store.transaction() as db:
                    self.assertEqual(1,db.execute('SELECT count(*) FROM game_transport_saves').fetchone()[0])
            finally:server.server_close()

    def test_remove_player_confirms_absent_input_after_committed_save(self):
        class FakeSocket:
            def __init__(self):self.replies=[]
            def sendall(self,data):self.replies.append(data)
        class State:game=None
        h=Handler.__new__(Handler);h.server=State();h.request=FakeSocket();h.actor=1
        h.identity={'account':'test','realm':'test'};h.room={'players':{1:h}};h.save_cycles={}
        h.quantum_request={'config':{'PlayerCount':4,'InputFixedSize':47}}
        h.last_input_tick=300;h.session_started=0
        raw,_=pack('RemovePlayer',value=0)
        with patch('pathlib.Path.write_bytes'):
            self.assertTrue(h.operation(253,{244:100,245:raw}))
        self.assertTrue(h.player_removed);self.assertEqual(302,h.last_input_tick)
        self.assertEqual(2,len(h.request.replies))
        for n,reply in enumerate(h.request.replies):
            self.assertEqual(102,reply[9])
            payload=Reader(reply[10:]).params()[245];bits=Bits(payload)
            bits.read(32+64+64);self.assertEqual(4,bits.read(8));self.assertEqual(47,bits.read(10))
            while bits.pos%8:bits.read(1)
            self.assertEqual(301+n,bits.read(32));self.assertEqual(1,bits.read(1))
            self.assertEqual(15,bits.read(4));self.assertEqual(15,bits.read(4))
        h.save_cycles={9:SimpleNamespace(committed=False)}
        h.player_removed=False;h.request.replies.clear()
        with patch('pathlib.Path.write_bytes'),self.assertRaises(ValueError):
            h.operation(253,{244:100,245:raw})
        self.assertFalse(h.player_removed);self.assertEqual(302,h.last_input_tick)
        self.assertEqual([],h.request.replies)
        h.save_cycles={}
        all_slots,_=pack('RemovePlayer',value=-1)
        with patch('pathlib.Path.write_bytes'):
            self.assertTrue(h.operation(253,{244:100,245:all_slots}))
        self.assertTrue(h.player_removed);self.assertEqual(304,h.last_input_tick)
        self.assertEqual(2,len(h.request.replies))

    def test_init_ping_key_exchange_and_operation(self):
        server=Server(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def exact(sock,n):
            result=b''
            while len(result)<n:
                chunk=sock.recv(n-len(result))
                if not chunk:raise EOFError
                result+=chunk
            return result
        def packet(sock):
            self.assertEqual(b'\xfb',exact(sock,1));h=exact(sock,6)
            return exact(sock,struct.unpack('>I',h[:4])[0]-7)
        try:
            with socket.create_connection(server.server_address,timeout=3) as sock:
                sock.sendall(frame(b'\xf3\x00test'))
                self.assertEqual(b'\xf3\x01\x00',packet(sock))
                sock.sendall(b'\xf0\x00\x00\x00\x05')
                self.assertEqual(b'\xf0',exact(sock,1));self.assertEqual(b'\x00\x00\x00\x05',exact(sock,8)[4:])
                key=KeyExchange();sock.sendall(frame(b'\xf3\x06\x00'+parameters({1:key.raw(key.public)})))
                reply=packet(sock);self.assertEqual(b'\xf3\x07\x00\x00\x00\x08',reply[:6])
                key.derive(Reader(reply[6:]).params()[1])
                from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
                from cryptography.hazmat.primitives.padding import PKCS7
                plaintext=b'\xe6'+parameters({225:'synthetic-player'})
                pad=PKCS7(128).padder();padded=pad.update(plaintext)+pad.finalize()
                enc=Cipher(algorithms.AES(key.key),modes.CBC(bytes(16))).encryptor()
                sock.sendall(frame(b'\xf3\x82'+enc.update(padded)+enc.finalize()))
                self.assertEqual(b'\xf3\x03\xe6\xff\x7f',packet(sock)[:5])
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main(verbosity=2)
