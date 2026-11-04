"""Receiving-client packet checks for Photon chat recipient semantics."""
import unittest
from unittest.mock import patch

from photon_wire import Reader,IntArray,parameters
from tools.photon_tcp_probe import Server,Handler


class ChatRoutingTests(unittest.TestCase):
    def setUp(self):
        self.logger=patch('tools.photon_tcp_probe.emit');self.logger.start()
        self.server=Server(('127.0.0.1',0),Handler,allow_room_probe=True,shared_simulation=True)
        self.room={'players':{},'timeline':object()}
        class FakeSocket:
            def __init__(self):self.packets=[]
            def sendall(self,data):self.packets.append(data)
        for actor in (1,2,3):
            peer=Handler.__new__(Handler);peer.server=self.server;peer.request=FakeSocket()
            peer.identity={'account':'test-account-'+str(actor),'realm':'test-realm'}
            peer.actor=actor;peer.room=self.room
            self.room['players'][actor]=peer
    def tearDown(self):self.server.server_close();self.logger.stop()
    def send(self,actor,payload=b'opaque-chat-message',**options):
        params={244:7,245:payload};params.update({int(k):v for k,v in options.items()})
        params=Reader(parameters(params)).params()
        self.assertTrue(self.room['players'][actor].operation(253,params))
    def received(self,actor):
        result=[]
        for packet in self.room['players'][actor].request.packets:
            self.assertEqual(bytes([0xf3,4,7]),packet[7:10])
            result.append(Reader(packet[10:]).params())
        return result

    def test_default_others_does_not_echo_sender_and_preserves_message(self):
        payload='你好，测试聊天'.encode('utf-8')
        self.send(1,payload)
        self.assertEqual([],self.received(1))
        for actor in (2,3):
            packets=self.received(actor)
            self.assertEqual(1,len(packets));self.assertEqual(1,packets[0][254]);self.assertEqual(payload,packets[0][245])

    def test_each_player_sends_one_line_without_receiving_own_server_copy(self):
        self.send(1,b'first');self.send(2,b'second')
        self.assertEqual([(2,b'second')],[(p[254],p[245]) for p in self.received(1)])
        self.assertEqual([(1,b'first')],[(p[254],p[245]) for p in self.received(2)])
        self.assertEqual([(1,b'first'),(2,b'second')],[(p[254],p[245]) for p in self.received(3)])

    def test_intentional_repeated_text_is_not_deduplicated(self):
        self.send(1,b'same');self.send(1,b'same')
        self.assertEqual([],self.received(1));self.assertEqual(2,len(self.received(2)))

    def test_explicit_others_and_all_receiver_groups(self):
        self.send(1,b'others',**{'246':0})
        self.send(1,b'all',**{'246':1})
        self.assertEqual([b'all'],[p[245] for p in self.received(1)])
        self.assertEqual([b'others',b'all'],[p[245] for p in self.received(2)])

    def test_explicit_targets_are_unique_and_override_receiver_group(self):
        self.send(1,b'targeted',**{'252':IntArray((2,2,999)),'246':1})
        self.assertEqual([],self.received(1));self.assertEqual(1,len(self.received(2)));self.assertEqual([],self.received(3))

    def test_master_group_targets_only_current_master(self):
        self.send(2,b'master',**{'246':2})
        self.assertEqual(1,len(self.received(1)));self.assertEqual([],self.received(2));self.assertEqual([],self.received(3))

    def test_departing_peer_does_not_block_other_recipient_or_disconnect_sender(self):
        with patch.object(self.room['players'][2].request,'sendall',side_effect=OSError('peer closed')):
            self.send(1,b'one remaining recipient')
        self.assertEqual([],self.received(1));self.assertEqual(1,len(self.received(3)))
