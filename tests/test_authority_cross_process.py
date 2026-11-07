import threading
import time
import unittest

from tools.authority_probe.cross_relay import CrossProcessRelay
from tools.probe_authority_cross_process import summarize


class Script:
    def __init__(self):
        self.messages = []
        self.condition = threading.Condition()

    def post(self, message, data):
        with self.condition:
            self.messages.append((message, data))
            self.condition.notify_all()

    def wait_for(self, predicate):
        with self.condition:
            return self.condition.wait_for(lambda: any(predicate(m) for m, _ in self.messages), timeout=2)


class CrossProcessRelayTests(unittest.TestCase):
    def test_binary_input_and_snapshots_cross_distinct_streams_and_recover_from_fault(self):
        source, receiver = Script(), Script()
        relay = CrossProcessRelay(source)
        relay.receiver = receiver
        try:
            relay.publish({'tick': 1, 'checksum': 'abc'}, b'full-state')
            self.assertTrue(receiver.wait_for(lambda m: m['payload']['tick'] == 1))
            relay.acknowledge(1)
            relay.input({'sequence': 0, 'tick': 1}, b'x' * 47)
            self.assertTrue(source.wait_for(lambda m: m['payload']['sequence'] == 0))
            self.assertEqual(source.messages[0][1], b'x' * 47)
            relay.arm_fault(0)
            relay.publish({'tick': 300, 'checksum': 'abc'}, b'fault-state')
            relay.input({'sequence': 1, 'tick': 300}, b'y' * 47)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and relay.report()['inputs_dropped'] < 1:
                threading.Event().wait(0.005)
            self.assertEqual(relay.report()['inputs_dropped'], 1)
            relay.publish({'tick': 480, 'checksum': 'abc'}, b'recovered-state')
            self.assertTrue(receiver.wait_for(lambda m: m['payload']['tick'] == 480))
            relay.input({'sequence': 2, 'tick': 480}, b'z' * 47)
            self.assertTrue(source.wait_for(lambda m: m['payload']['sequence'] == 2))
            self.assertEqual([m['payload']['sequence'] for m, _ in source.messages], [0, 2])
            self.assertFalse(relay.report()['errors'])
        finally:
            relay.close()
        self.assertTrue(all(not t.is_alive() for t in relay.threads))

    def test_replayed_input_is_rejected_and_malformed_packet_cannot_be_sent(self):
        source = Script()
        relay = CrossProcessRelay(source)
        try:
            with self.assertRaises(ValueError):
                relay.input({'sequence': 0, 'tick': 0}, b'too short')
            relay.input({'sequence': 1, 'tick': 10}, b'x' * 47)
            self.assertTrue(source.wait_for(lambda m: m['payload']['sequence'] == 1))
            relay.input({'sequence': 1, 'tick': 10}, b'x' * 47)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not relay.report()['errors']:
                threading.Event().wait(0.005)
            self.assertTrue(relay.report()['errors'])
            self.assertEqual(len(source.messages), 1)
        finally:
            relay.close()

    def test_receiver_without_ack_has_one_inflight_and_only_the_latest_pending_state(self):
        receiver = Script()
        relay = CrossProcessRelay(Script())
        relay.receiver = receiver
        try:
            relay.publish({'tick': 1, 'checksum': 'abc'}, b'one')
            self.assertTrue(receiver.wait_for(lambda m: m['payload']['tick'] == 1))
            for tick in range(2, 102):
                relay.publish({'tick': tick, 'checksum': 'abc'}, b'latest')
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and relay.report()['pending_tick'] != 101:
                threading.Event().wait(0.005)
            self.assertEqual(len(receiver.messages), 1)
            self.assertEqual(relay.report()['pending_tick'], 101)
            relay.acknowledge(1)
            self.assertTrue(receiver.wait_for(lambda m: m['payload']['tick'] == 101))
            self.assertEqual(len(receiver.messages), 2)
            with self.assertRaises(ValueError):
                relay.acknowledge(50)
        finally:
            relay.close()

    def test_reliable_command_waits_only_for_its_peer_and_is_delivered_after_recovery(self):
        source = Script()
        slow, healthy = CrossProcessRelay(source), CrossProcessRelay(source, slot=1, inject_fault=False)
        try:
            slow.arm_fault(0)
            slow.publish({'tick': 300, 'checksum': 'abc'}, b'fault')
            slow.command({'sequence': 0}, b'slow-command')
            healthy.command({'sequence': 0}, b'healthy-command')
            self.assertTrue(source.wait_for(lambda m: m['type'] == 'authority-command' and m['payload']['slot'] == 1))
            self.assertFalse(any(data == b'slow-command' for _, data in source.messages))
            slow.publish({'tick': 480, 'checksum': 'abc'}, b'recovered')
            self.assertTrue(source.wait_for(lambda m: m['type'] == 'authority-command' and m['payload']['slot'] == 0))
            self.assertEqual(sum(data == b'slow-command' for _, data in source.messages), 1)
            self.assertFalse(slow.report()['errors'])
        finally:
            slow.close()
            healthy.close()


class CrossProcessEvidenceTests(unittest.TestCase):
    def proof(self):
        records = [('source', {'event': 'stream-view-ready', 'boundHero': True, 'clientFlow': 4}),
                   ('receiver', {'event': 'receiver-view-ready', 'boundHero': True, 'clientFlow': 4})]
        for tick in range(900, 1806, 6):
            records.append(('source', {'event': 'stream-snapshot', 'tick': tick, 'checksum': str(tick),
                                      'player': {'position': [str(tick * 100), '0', '0']}, 'input': {'sequence': tick}}))
            if not 1200 <= tick < 1380:
                records.append(('receiver', {'event': 'receiver-state-verified', 'tick': tick,
                                            'checksum': str(tick), 'recovered': tick == 1380}))
        records.append(('receiver', {'event': 'receiver-stalled', 'lastTick': 1194}))
        transport = {'fault_start': 1200, 'fault_end': 1380, 'inputs_forwarded': 200,
                     'inputs_dropped': 50, 'states_dropped': 30, 'errors': []}
        return records, transport

    def test_real_checksums_movement_views_and_fault_are_all_required(self):
        records, transport = self.proof()
        self.assertTrue(summarize(records, transport)['probe_passed'])
        for _, p in records:
            if p.get('event') == 'receiver-state-verified':
                p['checksum'] = 'wrong'
        self.assertFalse(summarize(records, transport)['probe_passed'])

    def test_startup_stall_does_not_prove_recovery_from_the_injected_outage(self):
        records, transport = self.proof()
        records[-1][1]['lastTick'] = 900
        self.assertFalse(summarize(records, transport)['probe_passed'])
