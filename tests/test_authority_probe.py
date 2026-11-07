import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.probe_authority_runtime import main, summarize, summarize_authority, summarize_stream


def progress(frame):
    return {'event': 'authority-progress', 'state': 4, 'game': True,
            'quantum': True, 'frame': frame, 'simulated': str(frame - 120)}


def evidence():
    return [
        {'event': 'save-isolation-installed'},
        {'event': 'authority-session-created', 'headless': True, 'boundView': False, 'networkParticipants': 0},
        progress(180), progress(240), progress(300),
        {'event': 'authority-snapshot', 'tick': 300, 'bytes': 94000, 'checksum': 'abc'},
        {'event': 'authority-snapshot-roundtrip', 'tick': 300, 'restoredTick': 300,
         'expected': 'abc', 'actual': 'abc', 'matches': True},
        {'event': 'authority-session-restored', 'tick': 420}, progress(480), progress(540),
        {'event': 'authority-complete'},
    ]


class AuthorityProbeTests(unittest.TestCase):
    def test_measured_simulation_snapshot_and_recovery_pass(self):
        report = summarize(evidence())
        self.assertTrue(report['probe_passed'])
        self.assertFalse(report['networked_state_sync_verified'])

    def test_completion_marker_and_login_cannot_substitute_for_simulation(self):
        self.assertFalse(summarize([{'event': 'authority-complete'}])['probe_passed'])

    def test_checksum_disagreement_fails_even_with_matches_flag(self):
        records = evidence()
        records[6]['actual'] = 'different'
        self.assertFalse(summarize(records)['probe_passed'])

    def test_progress_before_restore_cannot_prove_recovery(self):
        records = evidence()
        records[7]['tick'] = 540
        self.assertFalse(summarize(records)['probe_passed'])

    def test_missing_restore_or_isolation_fails(self):
        for event in ('authority-session-restored', 'save-isolation-installed'):
            with self.subTest(event=event):
                self.assertFalse(summarize([p for p in evidence() if p.get('event') != event])['probe_passed'])

    def test_script_or_runtime_error_invalidates_success(self):
        self.assertFalse(summarize(evidence(), script_errors=1)['probe_passed'])
        self.assertFalse(summarize(evidence() + [{'event': 'authority-probe-error', 'error': 'failed'}])['probe_passed'])

    def test_unknown_build_is_rejected_before_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'GameAssembly.dll').write_bytes(b'unsupported')
            with patch('tools.probe_authority_runtime.run_probe') as launch, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(2, main(['--game-directory', directory]))
                launch.assert_not_called()

    def test_second_session_is_required_when_requested(self):
        self.assertFalse(summarize(evidence(), replicas=2)['probe_passed'])
        records = evidence() + [
            {'event': 'replica-state-verified', 'tick': 300, 'expected': 'abc', 'actual': 'abc', 'matches': True},
            {'event': 'replica-progress', 'state': 4, 'frame': 420},
        ]
        self.assertTrue(summarize(records, replicas=2)['probe_passed'])
        records[-1]['state'] = 6
        self.assertFalse(summarize(records, replicas=2)['probe_passed'])


class StreamProbeEvidenceTests(unittest.TestCase):
    def proof(self):
        records = [{'event': 'save-isolation-installed'},
                   {'event': 'stream-sessions-created', 'replicas': 2, 'boundViews': 0}]
        for tick in range(300, 1200, 6):
            records.append({'event': 'stream-snapshot', 'tick': tick, 'bytes': 94000, 'checksum': str(tick)})
            records.append({'event': 'stream-state-verified', 'replica': 2, 'tick': tick,
                            'checksum': str(tick), 'matches': True})
            if tick == 714:
                records.append({'event': 'stream-replica-stalled', 'replica': 1})
            if not 600 <= tick < 780:
                records.append({'event': 'stream-state-verified', 'replica': 1, 'tick': tick,
                                'checksum': str(tick), 'matches': True})
            if tick % 60 == 0:
                records.append({'event': 'stream-authority-progress', 'tick': tick})
        records.append({'event': 'stream-complete'})
        relay = {'peers': [{'replica': 1, 'dropped': 30}, {'replica': 2, 'dropped': 0}], 'errors': []}
        return records, relay

    def test_stream_requires_matching_source_checksums_and_recovery(self):
        records, relay = self.proof()
        self.assertTrue(summarize_stream(records, relay)['probe_passed'])
        for p in records:
            if p.get('event') == 'stream-state-verified':
                p['checksum'] = 'incorrect'
        self.assertFalse(summarize_stream(records, relay)['probe_passed'])

    def test_world_ticks_cannot_substitute_for_requested_player_or_view(self):
        records, relay = self.proof()
        self.assertFalse(summarize_stream(records, relay, require_player=True)['probe_passed'])
        self.assertFalse(summarize_stream(records, relay, require_view=True)['probe_passed'])
        self.assertTrue(summarize_stream(records, relay)['loopback_state_transport_verified'])

    def test_no_fault_or_healthy_updates_cannot_pass(self):
        records, relay = self.proof()
        relay['peers'][0]['dropped'] = 0
        self.assertFalse(summarize_stream(records, relay)['probe_passed'])
        relay['peers'][0]['dropped'] = 30
        records = [p for p in records if not (p.get('event') == 'stream-state-verified'
                   and p.get('replica') == 2 and 600 <= p['tick'] < 780)]
        self.assertFalse(summarize_stream(records, relay)['probe_passed'])

    def test_connection_errors_or_missing_source_evidence_fail(self):
        records, relay = self.proof()
        self.assertFalse(summarize_stream(records, relay, script_errors=1)['probe_passed'])
        relay['errors'] = ['connection ended']
        self.assertFalse(summarize_stream(records, relay)['probe_passed'])
        relay['errors'] = []
        self.assertFalse(summarize_stream([p for p in records if p.get('event') != 'stream-snapshot'], relay)['probe_passed'])


class AuthorityGameplayEvidenceTests(unittest.TestCase):
    def test_requires_a_live_character_over_time_and_requested_view_binding(self):
        records = [{'event': 'stream-authority-progress', 'tick': tick,
                    'activePlayers': 1, 'hero': '100000001'} for tick in (300, 600, 900)]
        records.append({'event': 'stream-complete'})
        self.assertTrue(summarize_authority(records)['probe_passed'])
        self.assertFalse(summarize_authority(records, require_view=True)['probe_passed'])
        records.append({'event': 'stream-view-ready', 'clientFlow': 4, 'boundHero': True})
        self.assertTrue(summarize_authority(records, require_view=True)['probe_passed'])
        records.append({'event': 'authority-probe-error', 'error': 'simulation exception'})
        self.assertFalse(summarize_authority(records)['probe_passed'])

    def test_empty_world_cannot_pass_a_gameplay_probe(self):
        records = [{'event': 'stream-authority-progress', 'tick': tick,
                    'activePlayers': 0, 'hero': '0'} for tick in (300, 600, 900)]
        records.append({'event': 'stream-complete'})
        self.assertFalse(summarize_authority(records)['probe_passed'])
