"""Validate v29466 independent simulation and snapshot restore in an isolated game.

Development only. Requires the installed game, Frida and a graphics device.
This does not enable networked authoritative state synchronization.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def verify_installation(game: Path) -> dict:
    expected = json.loads((ROOT / 'research/client-fingerprint.json').read_text())
    results = {}
    for name, fingerprint in expected.items():
        path = game / name
        if not path.is_file():
            raise ValueError(f'Required game file is missing: {path}')
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != fingerprint['sha256']:
            raise ValueError(f'Unsupported game build: {name}; refusing to install runtime hooks')
        results[name] = digest
    return results


def summarize(payloads: list[dict], script_errors: int = 0, replicas: int = 1) -> dict:
    """Require measured evidence, rather than trusting a completion marker."""
    progress = [p for p in payloads if p.get('event') == 'authority-progress'
                and p.get('state') == 4 and p.get('game') is True
                and p.get('quantum') is True and isinstance(p.get('frame'), int)]
    frames = [p['frame'] for p in progress]
    advanced = (len(frames) >= 3 and frames[-1] - frames[0] >= 120
                and int(progress[-1].get('simulated', 0)) > int(progress[0].get('simulated', 0)))
    snapshots = [p for p in payloads if p.get('event') == 'authority-snapshot'
                 and isinstance(p.get('bytes'), int) and 0 < p['bytes'] <= 64 * 1024 * 1024]
    roundtrips = [p for p in payloads if p.get('event') == 'authority-snapshot-roundtrip'
                  and p.get('matches') is True and p.get('expected')
                  and p.get('expected') == p.get('actual')
                  and p.get('tick') == p.get('restoredTick')
                  and any(s.get('tick') == p.get('tick')
                          and s.get('checksum') == p.get('expected') for s in snapshots)]
    restore_index = next((i for i, p in enumerate(payloads)
                          if p.get('event') == 'authority-session-restored'), None)
    resumed = False
    restore_tick = None
    if restore_index is not None:
        restore_tick = payloads[restore_index].get('tick')
        if isinstance(restore_tick, int):
            resumed = any(p.get('event') == 'authority-progress' and p.get('state') == 4
                          and p.get('game') is True and p.get('quantum') is True
                          and isinstance(p.get('frame'), int) and p['frame'] >= restore_tick + 120
                          for p in payloads[restore_index + 1:])
    errors = [p for p in payloads if p.get('event') == 'authority-probe-error']
    isolated = any(p.get('event') == 'save-isolation-installed' for p in payloads)
    created = any(p.get('event') == 'authority-session-created' and p.get('headless') is True
                  and p.get('boundView') is False and p.get('networkParticipants') == 0
                  for p in payloads)
    replica_match = any(p.get('event') == 'replica-state-verified' and p.get('expected')
                        and p.get('expected') == p.get('actual') and p.get('matches') is True
                        for p in payloads)
    replica_resumed = False
    for i, p in enumerate(payloads):
        if p.get('event') == 'replica-state-verified' and isinstance(p.get('tick'), int):
            replica_resumed = any(q.get('event') == 'replica-progress' and q.get('state') == 4
                                  and isinstance(q.get('frame'), int) and q['frame'] >= p['tick'] + 120
                                  for q in payloads[i + 1:])
            if replica_resumed:
                break
    passed = bool(isolated and created and advanced and roundtrips and resumed
                  and not errors and not script_errors
                  and (replicas == 1 or (replica_match and replica_resumed)))
    return {
        'probe_passed': passed,
        'save_isolation_observed': isolated,
        'independent_session_created': created,
        'simulation_advanced': bool(advanced),
        'first_observed_frame': frames[0] if frames else None,
        'last_observed_frame': frames[-1] if frames else None,
        'snapshot_roundtrip_verified': bool(roundtrips),
        'snapshot_sizes': [s['bytes'] for s in snapshots],
        'session_restore_tick': restore_tick,
        'simulation_resumed_after_restore': resumed,
        'replica_state_restore_verified': replica_match,
        'replica_resumed_after_restore': replica_resumed,
        'probe_errors': [p.get('error') for p in errors],
        'script_errors': script_errors,
        'networked_state_sync_verified': False,
    }


def summarize_stream(payloads, relay, script_errors=0, require_player=False, require_view=False):
    sources = {p['tick']: p for p in payloads if p.get('event') == 'stream-snapshot'
               and isinstance(p.get('tick'), int) and 0 < p.get('bytes', 0) <= 64 * 1024 * 1024}
    verified = [p for p in payloads if p.get('event') == 'stream-state-verified' and p.get('matches') is True
                and p.get('tick') in sources and p.get('checksum') == sources[p['tick']].get('checksum')]
    healthy = [p for p in verified if p.get('replica') == 2 and 600 <= p.get('tick', -1) < 780]
    stall_index = next((i for i, p in enumerate(payloads)
                        if p.get('event') == 'stream-replica-stalled' and p.get('replica') == 1), None)
    stalled = stall_index is not None
    recovered = bool(stalled and any(p in verified and p.get('replica') == 1 and p.get('tick', -1) >= 780
                                    for p in payloads[stall_index + 1:]))
    ticks = [p['tick'] for p in payloads if p.get('event') == 'stream-authority-progress']
    errors = [p.get('error') for p in payloads if p.get('event') == 'authority-probe-error']
    complete = any(p.get('event') == 'stream-complete' for p in payloads)
    created = any(p.get('event') == 'stream-sessions-created' and p.get('replicas') == 2
                  and p.get('boundViews') == 0 for p in payloads)
    isolated = any(p.get('event') == 'save-isolation-installed' for p in payloads)
    peers = {p['replica']: p for p in relay.get('peers', [])}
    counts = {index: sum(p.get('replica') == index for p in verified) for index in (1, 2)}
    player_observed = any(p.get('event') == 'stream-authority-progress' and p.get('activePlayers', 0) > 0
                          and p.get('hero') not in (None, '0') for p in payloads)
    view_observed = any(p.get('event') == 'stream-view-ready' and p.get('clientFlow') == 4
                        and p.get('boundHero') is True for p in payloads)
    transport_passed = bool(complete and isolated and created and len(healthy) >= 5 and stalled and recovered
                  and ticks and ticks[-1] >= 1100 and ticks[-1] - ticks[0] >= 600
                  and all(b > a for a, b in zip(ticks, ticks[1:]))
                  and all(count >= 30 for count in counts.values())
                  and peers.get(1, {}).get('dropped', 0) > 0
                  and not errors and not relay.get('errors') and not script_errors)
    passed = transport_passed and (not require_player or player_observed) and (not require_view or view_observed)
    return {'probe_passed': passed, 'stream_snapshots_verified': counts,
            'healthy_updates_during_other_client_fault': len(healthy),
            'slow_replica_prediction_stalled': stalled, 'slow_replica_recovered': recovered,
            'authority_first_tick': ticks[0] if ticks else None,
            'authority_last_tick': ticks[-1] if ticks else None,
            'probe_errors': errors, 'script_errors': script_errors, 'relay': relay,
            'networked_state_sync_verified': False,
            'loopback_state_transport_verified': transport_passed,
            'authority_player_observed': player_observed, 'player_view_observed': view_observed,
            'scope': 'loopback TCP with three game sessions inside one process; no player UI or input uplink'}


def summarize_authority(payloads, script_errors=0, require_view=False):
    progress = [p for p in payloads if p.get('event') == 'stream-authority-progress']
    live = [p for p in progress if p.get('activePlayers', 0) > 0 and p.get('hero') not in (None, '0')]
    stable = bool(len(live) >= 3 and live[-1]['tick'] - live[0]['tick'] >= 600)
    view = any(p.get('event') == 'stream-view-ready' and p.get('clientFlow') == 4
               and p.get('boundHero') is True for p in payloads)
    errors = [p.get('error') for p in payloads if p.get('event') == 'authority-probe-error']
    complete = any(p.get('event') == 'stream-complete' for p in payloads)
    return {'probe_passed': bool(stable and complete and not errors and not script_errors
                                 and (not require_view or view)),
            'authority_player_observed': stable, 'player_view_observed': view,
            'first_live_tick': live[0]['tick'] if live else None,
            'last_live_tick': live[-1]['tick'] if live else None,
            'probe_errors': errors, 'script_errors': script_errors,
            'networked_state_sync_verified': False, 'loopback_state_transport_verified': False,
            'scope': 'one game session; no replica or networked player verification'}


def run_probe(game: Path, fingerprints: dict, seconds: int, replicas: int = 1, stream=False, bind_view=False, player=False, authority_only=False, view_authority=False, windowed=False, nographics=False) -> tuple[dict, Path]:
    import frida  # The development dependency is not required by unit tests.

    device = frida.get_local_device()
    if any(p.name.lower() == 'norestforthewicked.exe' for p in device.enumerate_processes()):
        raise ValueError('A game is already running; the probe will not attach to that process')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lab = ROOT / 'dev-artifacts/authority-probe' / f'probe-{stamp}-{secrets.token_hex(3)}'
    lab.mkdir(parents=True)
    sys.path.insert(0, str(ROOT / 'server'))
    sys.path.insert(0, str(ROOT))
    from server import Store
    from game_backend import GameBackend, GameHandler
    from launcher_auth import login_account

    class QuietHandler(GameHandler):
        def log_message(self, *_args):
            pass

    store = Store(lab / 'backend.sqlite3', secrets.token_urlsafe(32))
    backend = GameBackend(store, 'http://127.0.0.1', transport_experiment=True)
    api = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
    api.daemon_threads = True
    api.store = store
    api.game = backend
    backend.base = f'http://127.0.0.1:{api.server_address[1]}/game'
    identity = login_account(store, '76561197960278073')
    config = {'endpoint': backend.base, 'token': backend.token(identity['account_id']), 'replicas': replicas, 'bindView': bind_view, 'player': player or bind_view or view_authority, 'authorityOnly': authority_only, 'viewAuthority': view_authority,
              'saveCache': str(lab / 'save-cache')}
    source = 'const probeConfig = ' + json.dumps(config) + ';\n'
    for name in ('bootstrap.js', 'runtime.js', 'stream.js' if stream else 'session.js'):
        source += (ROOT / 'tools/authority_probe' / name).read_text(encoding='utf-8') + '\n'
    # Config and token stay in memory; the injected source is never written out.
    records = []
    snapshots = []
    stop = threading.Event()
    detached = threading.Event()
    lock = threading.Lock()
    process_id = None
    session = None
    relay = None
    started = time.monotonic()
    threading.Thread(target=api.serve_forever, daemon=True).start()
    with (lab / 'events.jsonl').open('w', encoding='utf-8') as output:
        def receive(message, data):
            with lock:
                records.append(message)
                output.write(json.dumps({'elapsed': round(time.monotonic() - started, 3), **message}) + '\n')
                output.flush()
                payload = message.get('payload', {})
                if stream and payload.get('event') == 'stream-snapshot' and data is not None:
                    try:
                        if len(data) != payload.get('bytes'):
                            raise ValueError('Snapshot notification does not match its binary payload')
                        relay.publish(payload['tick'], payload['checksum'], data)
                    except (ValueError, TypeError, KeyError) as error:
                        records.append({'type': 'error', 'description': str(error)})
                        stop.set()
                    return
                if message.get('type') == 'error':
                    print(json.dumps({'event': 'script-error', 'description': message.get('description')}), flush=True)
                    stop.set()
                elif payload.get('event') == 'authority-snapshot' and data is not None:
                    tick = payload.get('tick')
                    if not isinstance(tick, int) or not 0 <= tick < 2**31 or len(data) != payload.get('bytes'):
                        records.append({'type': 'error', 'description': 'Invalid snapshot payload'})
                        stop.set()
                        return
                    target = lab / f'frame-{tick}.bin'
                    target.write_bytes(data)
                    snapshots.append({'file': target.name, 'bytes': len(data),
                                      'sha256': hashlib.sha256(data).hexdigest()})
                if payload.get('event') in ('authority-probe-error', 'authority-complete', 'stream-complete'):
                    stop.set()
                if payload.get('event') in ('authority-session-created', 'authority-progress', 'authority-snapshot',
                                            'authority-snapshot-roundtrip', 'authority-session-restoring',
                                            'authority-session-restored', 'authority-probe-error', 'authority-complete'):
                    print(json.dumps(payload), flush=True)
                elif payload.get('event') in ('replica-created', 'replica-state-verified', 'replica-progress'):
                    print(json.dumps(payload), flush=True)
                elif payload.get('event') in ('stream-sessions-created', 'stream-authority-progress',
                                              'stream-replica-stalled', 'stream-complete'):
                    print(json.dumps(payload), flush=True)

        try:
            game_args = [str(game / 'NoRestForTheWicked.exe'), '-logFile', str(lab / 'game.log')]
            if not windowed:
                game_args += ['-batchmode']
            if nographics:
                game_args += ['-nographics']
            if bind_view or windowed:
                game_args += ['-screen-width', '640', '-screen-height', '360', '-screen-fullscreen', '0']
            process_id = device.spawn(game_args, cwd=str(game), stdio='pipe')
            session = device.attach(process_id)
            session.on('detached', lambda *_args: detached.set())
            script = session.create_script(source)
            script.on('message', receive)
            script.load()
            if stream:
                from tools.authority_probe.relay import LoopbackRelay
                relay = LoopbackRelay(script)
            device.resume(process_id)
            print(json.dumps({'event': 'probe-started', 'pid': process_id, 'output': str(lab)}), flush=True)
            while time.monotonic() - started < seconds and not detached.is_set() and not stop.wait(0.5):
                pass
        finally:
            relay_report = relay.close() if relay is not None else {}
            if process_id is not None:
                try:
                    device.kill(process_id)
                except (frida.ProcessNotFoundError, frida.InvalidOperationError):
                    pass
            if session is not None:
                try:
                    session.detach()
                except frida.InvalidOperationError:
                    pass
            api.shutdown()
            api.server_close()
        with lock:
            payloads = [m.get('payload', {}) for m in records if m.get('type') == 'send']
            error_count = sum(m.get('type') == 'error' for m in records)
            if authority_only:
                report = summarize_authority(payloads, error_count, view_authority)
            else:
                report = summarize_stream(payloads, relay_report, error_count, player or bind_view or view_authority, bind_view or view_authority) if stream else summarize(payloads, error_count, replicas)
    report.update(duration_seconds=round(time.monotonic() - started, 2), snapshots=snapshots,
                  nographics_requested=nographics,
                  game_fingerprints=fingerprints)
    game_log = lab / 'game.log'
    game_errors = [line for line in game_log.read_text(encoding='utf-8', errors='replace').splitlines()
                   if '|X|' in line or '|E|' in line] if game_log.exists() else []
    report['null_graphics_device_observed'] = (game_log.exists() and
        'Renderer: Null Device' in game_log.read_text(encoding='utf-8', errors='replace'))
    report['game_exceptions'] = game_errors[:5]
    if game_errors:
        report['probe_passed'] = False
    # A metadata-only snapshot notification is insufficient evidence of export.
    if not stream and not snapshots:
        report['probe_passed'] = False
    (lab / 'result.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report, lab


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-directory', type=Path)
    parser.add_argument('--seconds', type=int, default=120, help='maximum probe duration, including game startup')
    parser.add_argument('--replicas', type=int, choices=(1, 2), default=1,
                        help='also restore the authority snapshot into a second independent session')
    parser.add_argument('--stream', action='store_true', help='continuous loopback TCP snapshots and a three-second receiver outage')
    parser.add_argument('--bind-view', action='store_true', help='also attempt loading the second replica into the game view')
    parser.add_argument('--player', action='store_true', help='insert an authority player into the stream experiment')
    parser.add_argument('--authority-only', action='store_true', help='diagnose one authority simulation without replica sessions')
    parser.add_argument('--view-authority', action='store_true', help='diagnose normal scene initialization on the authority')
    parser.add_argument('--windowed', action='store_true', help='launch a visible player window to diagnose rendering initialization')
    parser.add_argument('--nographics', action='store_true', help='add Unity -nographics for a headless feasibility probe')
    args = parser.parse_args(argv)
    try:
        game = args.game_directory
        if game is None:
            local = json.loads((ROOT / 'workspace.local.json').read_text(encoding='utf-8-sig'))
            game = Path(local['game_directory'])
        if not 30 <= args.seconds <= 600:
            raise ValueError('--seconds must be between 30 and 600')
        fingerprints = verify_installation(game)
        if (args.bind_view or args.player) and not args.stream:
            raise ValueError('--bind-view and --player require --stream')
        if args.authority_only and (not args.stream or args.bind_view):
            raise ValueError('--authority-only requires --stream and cannot bind a replica view')
        if args.view_authority and (not args.stream or args.bind_view):
            raise ValueError('--view-authority requires --stream and cannot bind a replica view')
        if args.nographics and (args.windowed or args.bind_view or args.view_authority):
            raise ValueError('--nographics cannot request a player window or a bound view')
        report, lab = run_probe(game.resolve(), fingerprints, args.seconds, args.replicas, args.stream, args.bind_view, args.player, args.authority_only, args.view_authority, args.windowed, args.nographics)
        print(json.dumps({'result': report, 'output': str(lab)}), flush=True)
        return 0 if report['probe_passed'] else 1
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
