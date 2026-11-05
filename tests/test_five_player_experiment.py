"""Isolated five-slot transport tests; do not enable the experiment by default."""
import copy
import json
from pathlib import Path
import unittest

from photon_wire import Reader
from quantum_wire import Bits
from room_simulation import ConfirmedRoom
from tests.test_room_simulation import Peer


class FivePlayerExperimentTests(unittest.TestCase):
    def setUp(self):
        self.request=json.loads((Path(__file__).resolve().parents[1]/'research/quantum-start-decoded.json').read_text())
        self.request['runtimeConfig']=bytes(self.request['runtimeConfig'])
        self.request['config']['PlayerCount']=5
        self.room=ConfirmedRoom(self.request,capacity=5,clock=lambda:10)

    def test_five_replicas_receive_identical_five_slot_frames_and_sixth_is_rejected(self):
        peers=[Peer(i) for i in range(1,6)]
        for peer in peers:
            self.room.start(peer,self.request)
            self.room.add_player(peer.actor,bytes([peer.actor])*8)
            if peer.actor!=1:
                self.room.snapshot(1,{'tick':self.room.tick,'total_size':4,'last':True,'data':b'snap'})
        self.assertEqual(list(range(5)),[self.room.players[p.actor].index for p in peers])
        tick=self.room.tick+1
        for peer in peers:
            self.room.submit(peer.actor,[{'slot':0,'tick':tick,'data':bytes([peer.actor])*47,'flags':1}])
        self.assertEqual(tick,self.room.tick)
        packets=[peer.inputs()[-1] for peer in peers]
        self.assertTrue(all(p==packets[0] for p in packets))
        bits=Bits(packets[0]);bits.read(32);bits.read(64);bits.read(64)
        self.assertEqual(5,bits.read(8));self.assertEqual(47,bits.read(10))
        with self.assertRaisesRegex(ValueError,'slots are occupied'):self.room.start(Peer(6),self.request)
        self.room.leave(5)
        replacement=self.room.start(Peer(10),self.request)
        self.assertEqual(4,replacement.index)

    def test_four_slot_default_rejects_five_slot_client(self):
        with self.assertRaisesRegex(ValueError,'configuration'):ConfirmedRoom(self.request)
        other=copy.deepcopy(self.request);other['config']['PlayerCount']=4
        with self.assertRaisesRegex(ValueError,'mismatch'):self.room.start(Peer(1),other)

    def test_fifth_slot_can_be_probed_with_one_real_client(self):
        room=ConfirmedRoom(self.request,capacity=5,first_slot=4)
        player=room.start(Peer(1),self.request)
        room.add_player(1,b'test-runtime-player')
        self.assertEqual(4,player.index)
        self.assertTrue(player.active)
