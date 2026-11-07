import copy,json,unittest
from pathlib import Path
from photon_wire import Reader
from quantum_wire import Bits,unpack
from room_simulation import ConfirmedRoom


class Peer:
    def __init__(self,actor):
        self.actor=actor;self.identity={'account':'player-'+str(actor)};self.packets=[];self.max_ping=10
    def send_packet(self,data):self.packets.append(data)
    def inputs(self):return [Reader(p[3:]).params()[245] for p in self.packets if p[:2]==b'\xf3\x04' and p[2]==102]
    def protocol(self):return [unpack(Reader(p[3:]).params()[245]) for p in self.packets if p[:3]==b'\xf3\x04\x64']


def frame_inputs(payload):
    frame=Bits(payload);frame.read(32+64+64)
    count=frame.read(8);size=frame.read(10)
    while frame.pos%8:frame.read(1)
    tick=frame.read(32);frame.read(1+count)
    entries=[]
    for _ in range(count):
        if frame.read(1):entries.append(None)
        else:
            data=bytes(frame.read(8) for _ in range(size)) if frame.read(1) else None
            entries.append({'data':data,'flags':frame.read(4)})
    for entry in entries:
        if entry is not None:entry['rpc']=frame.read_blob()
    return tick,entries


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

    def test_failed_snapshot_receiver_does_not_disconnect_supplier(self):
        self.start_first();self.room.start(self.b,self.request);self.room.add_player(2,b'new')
        def reject(_):raise OSError('receiver stopped reading')
        self.b.send_packet=reject
        self.room.snapshot(1,{'tick':121,'total_size':4,'last':True,'data':b'snap'})
        self.assertNotIn(2,self.room.players)
        self.assertTrue(self.room.players[1].active)
        self.room.submit(1,[self.input(122,1)])
        self.assertEqual(122,self.room.tick)

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

    def test_stalled_input_times_out_only_the_blocker_and_other_replica_resumes(self):
        events=[];self.room.log=lambda name,**data:events.append((name,data))
        self.start_first();self.join_second()
        self.room.submit(1,[self.input(124,1)])
        self.room.submit(2,[self.input(124,2)])
        # Player 1 has provided future inputs. Player 2 stops while its TCP
        # connection may still be alive. The shared confirmation cannot advance.
        self.room.submit(1,[self.input(tick,1) for tick in range(125,130)])
        packets=list(self.a.inputs());self.now+=6
        self.assertEqual([],self.room.check_stalls())
        stall=[data for name,data in events if name=='shared-input-stall'][-1]
        self.assertEqual([2],stall['blockers'])
        self.assertEqual(packets,self.a.inputs())
        self.now+=25
        self.assertEqual([self.b],self.room.check_stalls())
        self.assertEqual([],self.room.check_stalls(),'One disconnect request per expired replica')
        self.room.leave(2)  # The transport handler's normal EOF cleanup.
        self.assertEqual(129,self.room.tick)
        self.assertTrue(self.room.players[1].active)
        self.assertEqual([],self.room.check_stalls())
        self.room.submit(1,[self.input(130,3)])
        self.assertEqual(130,self.room.tick)

    def test_replaying_old_inputs_does_not_keep_a_blocker_alive(self):
        self.start_first();self.join_second()
        self.room.submit(1,[self.input(124,1)]);self.room.submit(2,[self.input(124,2)])
        self.room.submit(1,[self.input(125,1)])
        self.now+=31
        self.room.submit(2,[self.input(124,2)])
        self.assertEqual(self.now,self.room.players[2].last_input_at)
        self.assertEqual([self.b],self.room.check_stalls())

    def test_input_progress_recovery_cancels_timeout_before_expiry(self):
        self.start_first();self.join_second()
        self.room.submit(1,[self.input(124,1)]);self.room.submit(2,[self.input(124,2)])
        self.room.submit(1,[self.input(125,1)]);self.now+=29
        self.assertEqual([],self.room.check_stalls())
        self.room.submit(2,[self.input(125,2)]);self.now+=2
        self.assertEqual(125,self.room.tick);self.assertEqual([],self.room.check_stalls())
        self.assertEqual(self.a.inputs()[-1],self.b.inputs()[-1])

    def test_initial_loading_and_pending_snapshot_are_not_runtime_timeouts(self):
        self.start_first();self.room.start(self.b,self.request)
        self.now+=31
        self.assertEqual([],self.room.check_stalls())
        self.assertFalse(self.room.players[2].active)
        # First player has not submitted any forward inputs yet: two-minute grace.
        self.now+=90
        self.assertEqual([self.a],self.room.check_stalls())

    def test_timeout_can_be_disabled_without_disabling_diagnostics(self):
        events=[];self.room.log=lambda name,**data:events.append((name,data))
        self.room.stall_timeout=0
        self.start_first();self.join_second()
        self.room.submit(1,[self.input(124,1)]);self.room.submit(2,[self.input(124,2)])
        self.room.submit(1,[self.input(125,1)]);self.now+=500
        self.assertEqual([],self.room.check_stalls())
        self.assertTrue(any(name=='shared-input-stall' for name,_ in events))

    def test_normal_session_and_idle_controls_keep_advancing(self):
        self.start_first();self.join_second()
        for tick in range(124,424):
            self.now+=1/60
            self.room.submit(1,[self.input(tick,0)])
            self.room.submit(2,[self.input(tick,0)])
            self.assertEqual([],self.room.check_stalls())
        self.assertEqual(423,self.room.tick)
        self.assertEqual(self.a.inputs()[-300:],self.b.inputs()[-300:])

    def test_missing_input_falls_back_without_repeating_buttons(self):
        self.start_first();self.join_second()
        self.room.input_wait_timeout=.5
        self.room.submit(1,[self.input(124,1)]);self.room.submit(2,[self.input(124,2)])
        self.room.submit(1,[self.input(125,9)])
        self.now+=.51
        self.room.submit(1,[self.input(126,9)])
        self.assertEqual(125,self.room.tick)
        tick,entries=frame_inputs(self.b.inputs()[-1])
        self.assertEqual(125,tick)
        self.assertEqual({'data':bytes([9])*47,'flags':1,'rpc':None},entries[0])
        self.assertEqual({'data':bytes(47),'flags':4,'rpc':None},entries[1])
        self.assertEqual(self.a.inputs()[-1],self.b.inputs()[-1])
        self.assertEqual(bytes(47),self.room.players[2].last)
        self.now+=1/60
        self.room.submit(1,[self.input(127,9)])
        self.assertEqual(126,self.room.tick)
        self.assertTrue(self.room.players[2].fallback_active)
        self.now+=1/60
        self.room.submit(2,[self.input(127,3)])
        self.assertEqual(127,self.room.tick)
        self.assertFalse(self.room.players[2].fallback_active)

    def test_timer_expires_wait_without_more_packets_and_does_not_fast_forward(self):
        self.start_first();self.join_second()
        # This test exercises the timer boundary itself; keep its threshold
        # explicit so the production default can change independently.
        self.room.input_wait_timeout=.25
        self.room.submit(1,[self.input(124,1)]);self.room.submit(2,[self.input(124,2)])
        self.room.submit(1,[self.input(t,1) for t in range(125,135)])
        self.now+=.24;self.room.service_inputs();self.assertEqual(124,self.room.tick)
        self.now+=.02;self.room.service_inputs();self.assertEqual(125,self.room.tick)
        for _ in range(10):self.room.service_inputs()
        self.assertEqual(125,self.room.tick)
        for tick in range(126,135):
            self.now+=.02;self.room.service_inputs();self.assertIn(self.room.tick,(tick,tick+1))
        self.now+=100;self.room.service_inputs()
        self.assertEqual(134,self.room.tick,'No progress is fabricated when every player has stopped sending inputs')
        self.assertEqual(self.a.inputs()[-10:],self.b.inputs()[-10:])

    def test_late_input_never_rewrites_fallback_and_command_executes_once(self):
        self.start_first();self.join_second()
        self.room.submit(1,[self.input(124,1)]);self.room.submit(2,[self.input(124,2)])
        self.room.command(2,b'one-command')
        self.room.submit(1,[self.input(t,1) for t in range(125,128)])
        self.now+=.26;self.room.service_inputs()
        confirmed=self.b.inputs()[-1]
        self.assertEqual(b'one-command',frame_inputs(confirmed)[1][1]['rpc'])
        self.room.submit(2,[self.input(125,7)])
        self.assertEqual(confirmed,self.b.inputs()[-1])
        self.now+=.02;self.room.service_inputs()
        self.assertIsNone(frame_inputs(self.b.inputs()[-1])[1][1]['rpc'])
        self.now+=.02;self.room.submit(2,[self.input(127,8)])
        self.assertEqual(127,self.room.tick)
        self.assertEqual(bytes([8])*47,frame_inputs(self.b.inputs()[-1])[1][1]['data'])
        self.assertEqual(confirmed,self.b.inputs()[-3])
        self.assertEqual(self.a.inputs()[-4:],self.b.inputs()[-4:])

    def test_fallback_does_not_bypass_initial_loading_and_can_be_disabled(self):
        for timeout in (.25,0):
            with self.subTest(timeout=timeout):
                self.setUp();self.room.input_wait_timeout=timeout
                self.start_first();self.join_second()
                self.room.submit(1,[self.input(124,1)])
                self.now+=1;self.room.service_inputs();self.assertEqual(123,self.room.tick)
                if timeout==0:
                    self.room.submit(2,[self.input(124,2)])
                    self.room.submit(1,[self.input(125,1)])
                    self.now+=1;self.room.service_inputs();self.assertEqual(124,self.room.tick)

    def test_long_stalled_player_times_out_while_other_player_keeps_advancing(self):
        self.start_first();self.join_second()
        self.room.submit(1,[self.input(124,1)]);self.room.submit(2,[self.input(124,2)])
        self.room.submit(1,[self.input(125,1)])
        self.now+=.26;self.room.service_inputs()
        for _ in range(31):
            self.now+=1
            self.room.submit(1,[self.input(self.room.tick+1,1)])
            self.room.service_inputs()
        self.assertGreater(self.room.tick,150)
        self.assertEqual([self.b],self.room.check_stalls())
        self.assertTrue(self.room.players[1].active)

    def test_fallback_clock_retains_60hz_with_timer_jitter(self):
        self.start_first();self.join_second()
        self.room.submit(1,[self.input(124,1)]);self.room.submit(2,[self.input(124,2)])
        self.room.submit(1,[self.input(t,1) for t in range(125,225)])
        self.now+=.26;self.room.service_inputs()
        for _ in range(50):
            self.now+=.02;self.room.service_inputs()
        self.assertGreaterEqual(self.room.tick,185)
        self.assertLessEqual(self.room.tick,186)

if __name__=='__main__':unittest.main(verbosity=2)
