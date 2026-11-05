import copy,json,unittest
from pathlib import Path
from photon_wire import Reader
from quantum_wire import unpack
from room_simulation import ConfirmedRoom


class Peer:
    def __init__(self,actor):
        self.actor=actor;self.identity={'account':'player-'+str(actor)};self.packets=[];self.max_ping=10
    def send_packet(self,data):self.packets.append(data)
    def inputs(self):return [Reader(p[3:]).params()[245] for p in self.packets if p[:2]==b'\xf3\x04' and p[2]==102]
    def protocol(self):return [unpack(Reader(p[3:]).params()[245]) for p in self.packets if p[:3]==b'\xf3\x04\x64']


class SharedRoomTests(unittest.TestCase):
    def setUp(self):
        self.request=json.loads((Path(__file__).resolve().parents[1]/'research/quantum-start-decoded.json').read_text())
        self.request['runtimeConfig']=bytes(self.request['runtimeConfig'])
        self.now=10.0;self.room=ConfirmedRoom(self.request,clock=lambda:self.now)
        self.a=Peer(1);self.b=Peer(2)

    def start_first(self):
        self.room.start(self.a,self.request);self.room.add_player(1,b'first-player')

    def join_second(self):
        self.room.start(self.b,self.request)
        self.room.add_player(2,b'second-player')
        self.room.snapshot(1,{'tick':self.room.tick,'total_size':8,'last':False,'data':b'snap'})
        self.room.snapshot(1,{'tick':self.room.tick,'total_size':8,'last':True,'data':b'shot'})

    def input(self,tick,byte):return {'tick':tick,'slot':0,'data':bytes([byte])*47,'flags':1}

    def test_single_client_bootstrap_matches_validated_ticks(self):
        self.start_first();self.assertEqual(121,self.room.tick)
        self.assertEqual(2,len(self.a.inputs()))
        self.room.submit(1,[self.input(122,5),self.input(123,6)])
        self.assertEqual(123,self.room.tick)

    def test_late_join_waits_for_complete_snapshot_before_activation(self):
        self.start_first();self.room.start(self.b,self.request);self.room.add_player(2,b'new')
        self.assertFalse(self.room.players[2].ready);self.assertFalse(self.room.players[2].active)
        self.assertEqual('FrameSnapshotRequest',self.a.protocol()[-1]['name'])
        self.room.snapshot(1,{'tick':121,'total_size':8,'last':False,'data':b'abcd'})
        self.assertFalse(self.room.players[2].ready)
        self.room.snapshot(1,{'tick':121,'total_size':8,'last':True,'data':b'efgh'})
        self.assertTrue(self.room.players[2].active)
        self.assertEqual(123,self.room.tick)
        self.assertEqual(['FrameSnapshot','FrameSnapshot'],[x['name'] for x in self.b.protocol()[1:]])

    def test_two_inputs_produce_identical_confirmed_packets(self):
        self.start_first();self.join_second()
        self.room.submit(1,[self.input(124,1),self.input(125,2)])
        self.assertEqual(123,self.room.tick)
        self.room.submit(2,[self.input(124,3),self.input(125,4)])
        self.assertEqual(125,self.room.tick)
        self.assertEqual(self.a.inputs()[-4:],self.b.inputs()[-4:])

    def test_snapshot_catches_up_frames_after_snapshot_tick(self):
        self.start_first();self.room.start(self.b,self.request)
        self.room.submit(1,[self.input(122,1),self.input(123,2)])
        self.room.snapshot(1,{'tick':121,'total_size':4,'last':True,'data':b'snap'})
        self.assertEqual(self.a.inputs()[-2:],self.b.inputs())
        self.assertTrue(self.room.players[2].ready)

    def test_bad_or_unrequested_snapshot_rejected(self):
        self.start_first();self.room.start(self.b,self.request)
        with self.assertRaises(ValueError):self.room.snapshot(2,{'tick':121,'total_size':1,'last':True,'data':b'x'})
        with self.assertRaises(ValueError):self.room.snapshot(1,{'tick':121,'total_size':2,'last':True,'data':b'x'})
        self.assertFalse(self.room.players[2].ready)

    def test_remove_one_player_keeps_other_simulating(self):
        self.start_first();self.join_second();self.room.remove_player(1)
        self.assertFalse(self.room.players[1].active)
        tick=self.room.tick;self.room.submit(2,[self.input(tick+1,7)])
        self.assertEqual(tick+1,self.room.tick)
        self.assertEqual(self.a.inputs()[-1],self.b.inputs()[-1])

    def test_slot_reuse_is_independent_of_monotonic_actor_id(self):
        self.start_first();self.join_second();self.room.leave(1)
        c=Peer(9);self.room.start(c,self.request)
        self.assertEqual(0,self.room.players[9].index)
        self.assertEqual(2,self.room.snapshot_source)

    def test_commands_and_input_replays(self):
        self.start_first();self.room.command(1,b'command')
        self.room.submit(1,[self.input(122,1)])
        self.assertEqual(0,len(self.room.players[1].commands))
        self.room.submit(1,[self.input(122,1)])
        self.assertEqual(122,self.room.tick)
        with self.assertRaises(ValueError):self.room.submit(1,[self.input(9999,1)])

    def test_unconfirmed_input_revision_is_ordered_once(self):
        self.start_first();self.join_second()
        self.room.submit(1,[self.input(124,1)])
        self.room.submit(1,[self.input(124,2)])
        self.assertEqual(123,self.room.tick)
        self.room.submit(2,[self.input(124,3)])
        self.assertEqual(124,self.room.tick)
        self.assertEqual(bytes([2])*47,self.room.players[1].last)
        committed=self.a.inputs()[-1]
        self.room.submit(1,[self.input(124,4)])
        self.assertEqual(committed,self.a.inputs()[-1])
        self.assertEqual(committed,self.b.inputs()[-1])

if __name__=='__main__':unittest.main(verbosity=2)
