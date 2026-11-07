import socket,struct,threading,unittest,tempfile,time
import json
from types import SimpleNamespace
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch
from photon_wire import KeyExchange,Reader,frame,parameters,Byte
from transport import Server,Handler
from server import Store
from game_backend import GameBackend
from quantum_wire import pack,Bits
from save_wire import bundle
from transport_writer import QueuedWriter

class TransportTests(unittest.TestCase):
    def setUp(self):
        # Synthetic connections must not be mixed into live client evidence.
        self.log_patch=patch('transport.emit')
        self.log_patch.start();self.addCleanup(self.log_patch.stop)

    def test_heartbeats_and_frames_share_one_serialized_tcp_writer(self):
        entered=threading.Event();release=threading.Event()
        class PartialSocket:
            def __init__(self):self.data=bytearray();self.writes=0
            def sendall(self,data):
                self.writes+=1;self.data.extend(data[:3])
                if self.writes==1:
                    entered.set()
                    if not release.wait(3):raise TimeoutError('Test writer was not released')
                self.data.extend(data[3:])
        h=Handler.__new__(Handler);h.request=PartialSocket();h.send_lock=threading.RLock()
        payload=b'\xf3\x04frame-payload';ping=b'\x01\x02\x03\x04'
        first=threading.Thread(target=h.send_packet,args=(payload,))
        second=threading.Thread(target=h.send_ping,args=(ping,))
        first.start();self.assertTrue(entered.wait(3));second.start()
        try:
            second.join(.05)
            self.assertTrue(second.is_alive(),'Heartbeat must wait for the incomplete frame')
            self.assertEqual(1,h.request.writes)
        finally:release.set();first.join(3);second.join(3)
        self.assertFalse(first.is_alive());self.assertFalse(second.is_alive())
        expected=frame(payload);self.assertEqual(expected,h.request.data[:len(expected)])
        reply=h.request.data[len(expected):]
        self.assertEqual(9,len(reply));self.assertEqual(240,reply[0]);self.assertEqual(ping,reply[-4:])

    def test_real_socket_backpressure_does_not_block_confirmed_timeline(self):
        from room_simulation import ConfirmedRoom
        request=json.loads((Path(__file__).resolve().parents[1]/'research/quantum-start-decoded.json').read_text())
        request['runtimeConfig']=bytes(request['runtimeConfig'])
        now=[10.0];timeline=ConfirmedRoom(request,clock=lambda:now[0])
        sockets=[];peers=[];received=[];read_done=threading.Event()
        healthy_left,healthy_right=socket.socketpair()
        slow_left,slow_right=socket.socketpair()
        sockets.extend((healthy_left,healthy_right,slow_left,slow_right))
        slow_left.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,1024)
        slow_right.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024)
        def failed(peer,reason):
            try:peer.request.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        def read_healthy():
            def exact(size):
                data=b''
                while len(data)<size:
                    part=healthy_right.recv(size-len(data))
                    if not part:raise EOFError
                    data+=part
                return data
            try:
                while True:
                    self.assertEqual(b'\xfb',exact(1))
                    header=exact(6);body=exact(struct.unpack('>I',header[:4])[0]-7)
                    if body[:3]==b'\xf3\x04\x66':
                        payload=Reader(body[3:]).params()[245]
                        bits=Bits(payload);bits.read(32+64+64+8+10)
                        while bits.pos%8:bits.read(1)
                        received.append(bits.read(32))
            except (EOFError,OSError):pass
            finally:read_done.set()
        reader=threading.Thread(target=read_healthy);reader.start()
        try:
            for actor,connection in ((1,healthy_left),(2,slow_left)):
                peer=Handler.__new__(Handler);peer.actor=actor;peer.request=connection
                peer.identity={'account':str(actor)}
                peer.writer=QueuedWriter(connection,lambda reason,p=peer:failed(p,reason),clock=lambda:now[0])
                peers.append(peer);timeline.start(peer,request);timeline.add_player(actor,b'player')
                if actor==2:timeline.snapshot(1,{'tick':timeline.tick,'total_size':1,'last':True,'data':b's'})
            peers[1].writer.send(b'x'*(32*1024*1024))  # Fill the unread socket on Windows/Linux.
            item=lambda t,b:{'tick':t,'slot':0,'data':bytes([b])*47,'flags':1}
            timeline.submit(1,[item(124,1)]);timeline.submit(2,[item(124,2)])
            timeline.submit(1,[item(t,1) for t in range(125,135)])
            now[0]+=.26;timeline.service_inputs()
            for _ in range(9):now[0]+=.02;timeline.service_inputs()
            self.assertEqual(134,timeline.tick)
            peers[0].writer.drain(timeout=1)
            deadline=time.monotonic()+1
            while (not received or received[-1]<134) and time.monotonic()<deadline:time.sleep(.005)
            self.assertIn(134,received)
            self.assertEqual(list(range(124,135)),[tick for tick in received if tick>=124])
            now[0]+=5;peers[1].writer.check_timeout()
            self.assertTrue(peers[1].writer.closed)
            self.assertFalse(peers[0].writer.closed)
        finally:
            for peer in peers:peer.writer.close()
            for connection in sockets:
                try:connection.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                connection.close()
            reader.join(2)
            for peer in peers:peer.writer.thread.join(2)
            self.assertTrue(read_done.is_set())

    def test_watchdog_disconnects_stalled_live_socket_and_recovers_other_player(self):
        from room_simulation import ConfirmedRoom
        request=json.loads((Path(__file__).resolve().parents[1]/'research/quantum-start-decoded.json').read_text())
        request['runtimeConfig']=bytes(request['runtimeConfig'])
        now=[10.0];timeline=ConfirmedRoom(request,clock=lambda:now[0])
        server=Server(('127.0.0.1',0),Handler,allow_room_probe=True,shared_simulation=True)
        server._watchdog_stop.set();server._watchdog.join(3)
        sockets=[];threads=[];peers=[]
        class Saves:
            def __init__(self):self.departures=[]
            def player_left(self,actor):self.departures.append(actor)
        saves=Saves();room={'players':{},'timeline':timeline,'shared_saves':saves}
        server.rooms['realm']=room
        def wait_for_eof(peer):
            try:
                while peer.request.recv(1024):pass
            except OSError:
                pass
            finally:peer.leave_room()
        try:
            for actor in (1,2):
                left,right=socket.socketpair();sockets.extend((left,right))
                h=Handler.__new__(Handler);h.request=left;h.server=server;h.actor=actor
                h.identity={'account':str(actor),'realm':'realm'};h.room=room;h.send_lock=threading.RLock()
                room['players'][actor]=h;peers.append(h)
                timeline.start(h,request)
                if actor==1:timeline.add_player(1,b'a')
                else:
                    timeline.add_player(2,b'b')
                    timeline.snapshot(1,{'tick':timeline.tick,'total_size':1,'last':True,'data':b's'})
                t=threading.Thread(target=wait_for_eof,args=(h,));threads.append(t);t.start()
            item=lambda tick,value:{'tick':tick,'slot':0,'data':bytes([value])*47,'flags':1}
            timeline.submit(1,[item(124,1)]);timeline.submit(2,[item(124,2)])
            timeline.submit(1,[item(t,1) for t in range(125,130)])
            now[0]+=31;server.check_room_stalls();threads[1].join(3)
            self.assertFalse(threads[1].is_alive())
            self.assertTrue(threads[0].is_alive())
            self.assertEqual([2],saves.departures)
            self.assertEqual(129,timeline.tick)
            self.assertEqual([1],list(room['players']))
            timeline.submit(1,[item(130,1)]);self.assertEqual(130,timeline.tick)
        finally:
            for sock in sockets:
                try:sock.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                sock.close()
            for thread in threads:thread.join(3)
            server.server_close()
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

    def test_steam_id_nickname_event_routes_to_actor_not_room_max_players(self):
        class FakeSocket:
            def __init__(self):self.replies=[]
            def sendall(self,data):self.replies.append(data)
        server=Server(('127.0.0.1',0),Handler,allow_room_probe=True)
        try:
            h=Handler.__new__(Handler);h.server=server;h.request=FakeSocket()
            steam_id='76561197960278073'
            h.identity={'account':'account-guid','realm':'realm','name':steam_id};h.role='game';h.room=None
            h.operation(226,{255:'realm',249:{Byte(255):'persona'}})
            h.request.replies.clear()
            h.operation(252,{254:h.actor,251:{Byte(255):'persona'}})
            packet=h.request.replies[-1]
            self.assertEqual(bytes([0xf3,4,253]),packet[7:10])
            params=Reader(packet[10:]).params()
            # This is the receiving client's routing rule, independent of how
            # the server internally represents the player/room property maps.
            target_actor=params.get(253,0)
            self.assertEqual(h.actor,target_actor)
            self.assertNotEqual(0,target_actor,'A player nickname must not be read as room MaxPlayers')
            self.assertEqual(steam_id,params[251][Byte(255)])
            self.assertEqual({},h.room['properties'])
        finally:server.server_close()

    def test_room_property_event_uses_zero_target_and_unknown_actor_is_rejected(self):
        class FakeSocket:
            def __init__(self):self.replies=[]
            def sendall(self,data):self.replies.append(data)
        server=Server(('127.0.0.1',0),Handler,allow_room_probe=True)
        try:
            h=Handler.__new__(Handler);h.server=server;h.request=FakeSocket()
            h.identity={'account':'account-guid','realm':'realm'};h.role='game';h.room=None
            h.operation(226,{255:'realm'})
            h.request.replies.clear()
            h.operation(252,{251:{Byte(253):True}})
            params=Reader(h.request.replies[-1][10:]).params()
            self.assertEqual(0,params[253]);self.assertTrue(params[251][Byte(253)])
            before=dict(h.room['properties'])
            h.operation(252,{254:9999,251:{Byte(255):'76561197960278073'}})
            self.assertEqual(before,h.room['properties'])
            self.assertEqual(bytes([0xf3,3,252]),h.request.replies[-1][7:10])
        finally:server.server_close()

    def test_failed_broadcast_recipient_does_not_disconnect_sender(self):
        class Socket:
            def sendall(self,_):pass
        server=Server(('127.0.0.1',0),Handler,allow_room_probe=True)
        try:
            peers=[]
            for number in (1,2):
                peer=Handler.__new__(Handler);peer.server=server;peer.request=Socket()
                peer.identity={'account':str(number),'realm':'realm'};peer.role='game';peer.room=None
                peer.operation(226,{255:'realm'});peers.append(peer)
            def reject(_):raise OSError('closed recipient')
            peers[1].send_packet=reject
            self.assertTrue(peers[0].operation(252,{251:{Byte(253):True}}))
            self.assertIsNotNone(peers[0].room)
        finally:server.server_close()

    @unittest.skipUnless((Path(__file__).resolve().parents[1]/'research/save-bundle-reference.bin').is_file(),'Optional private native save fixture is not distributed')
    def test_real_save_bundle_commits_before_success_ack(self):
        root=Path(__file__).resolve().parents[1]/'research'
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
