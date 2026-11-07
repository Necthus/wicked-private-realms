"""Development-only, isolated two-process game state and input experiment."""
from __future__ import annotations

import argparse
import base64
import hashlib
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'server'))

from tools.probe_authority_runtime import verify_installation


def summarize(records, transport):
    source = [p for role, p in records if role == 'source' and p.get('event') == 'stream-snapshot']
    states = {p['tick']: p for p in source}
    verified = [p for role, p in records if role == 'receiver' and p.get('event') == 'receiver-state-verified'
                and p.get('tick') in states and p.get('checksum') == states[p['tick']].get('checksum')]
    source_view = any(role == 'source' and p.get('event') == 'stream-view-ready' and p.get('boundHero') is True
                      and p.get('clientFlow') == 4 for role, p in records)
    receiver_view = any(role == 'receiver' and p.get('event') == 'receiver-view-ready' and p.get('boundHero') is True
                        for role, p in records)
    positions = [p['player']['position'] for p in source if p.get('player', {}).get('position')
                 and p.get('input', {}).get('sequence', -1) >= 0]
    moved = bool(positions and any(max(int(p[axis]) for p in positions) - min(int(p[axis]) for p in positions) > 32768
                                   for axis in (0, 2)))
    faults = transport.get('fault_start'), transport.get('fault_end')
    stalled = bool(all(type(x) is int for x in faults) and any(
        role == 'receiver' and p.get('event') == 'receiver-stalled'
        and faults[0] - 12 <= p.get('lastTick', -1) < faults[1]
        for role, p in records))
    recovered = bool(stalled and any(p.get('recovered') is True and p['tick'] >= faults[1] for p in verified))
    progressed = bool(all(type(x) is int for x in faults) and
                      any(faults[0] <= p['tick'] < faults[1] for p in source) and
                      any(p['tick'] >= faults[1] + 300 for p in source))
    errors = [f'{role}: {p.get("error")}' for role, p in records if p.get('event') == 'authority-probe-error']
    passed = bool(source_view and receiver_view and len(verified) >= 60 and moved and stalled and recovered
                  and progressed and transport.get('inputs_forwarded', 0) >= 120
                  and transport.get('inputs_dropped', 0) > 0 and transport.get('states_dropped', 0) > 0
                  and not transport.get('errors') and not errors)
    return {'probe_passed': passed, 'independent_process_state_verified': len(verified),
            'authority_view_bound': source_view, 'receiver_view_bound': receiver_view,
            'authority_character_moved_after_network_input': moved,
            'receiver_prediction_stalled': stalled, 'receiver_recovered': recovered,
            'authority_advanced_through_receiver_outage': progressed,
            'probe_errors': errors, 'transport': transport,
            'networked_state_sync_verified': passed,
            'scope': 'two isolated processes, one synthetic player, loopback TCP, generated client movement; no multiplayer saves'}


def run(game, fingerprints, seconds, receivers=1, interactive=False, state_directory=None):
    import frida
    from server import Store
    from game_backend import GameBackend, GameHandler
    from launcher_auth import login_account
    from tools.authority_probe.cross_relay import CrossProcessRelay
    from authoritative_state import Snapshot
    from authoritative_checkpoints import CheckpointStore, CheckpointWriter

    device = frida.get_local_device()
    if any(p.name.lower() == 'norestforthewicked.exe' for p in device.enumerate_processes()):
        raise ValueError('A game is already running; refusing to attach or interrupt it')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    parent = Path(state_directory).resolve() / 'runs' if state_directory else ROOT / 'dev-artifacts/authority-probe'
    lab = parent / f'cross-{stamp}-{secrets.token_hex(3)}'
    lab.mkdir(parents=True)
    checkpoint_path = Path(state_directory).resolve() / 'checkpoints.sqlite3' if state_directory else lab / 'checkpoints.sqlite3'
    checkpoint_store = CheckpointStore(checkpoint_path)
    build = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
    saved_realms = checkpoint_store.realms()
    if len(saved_realms) > 1:
        raise ValueError('This local runner supports one authority realm per state directory')
    resumed_checkpoint = checkpoint_store.load(saved_realms[0], build) if saved_realms else None
    store = Store(lab / 'backend.sqlite3', secrets.token_urlsafe(32))
    backend = GameBackend(store, 'http://127.0.0.1', transport_experiment=True)

    class QuietHandler(GameHandler):
        def log_message(self, *_args):
            pass

    api = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
    api.daemon_threads = True
    api.store, api.game = store, backend
    backend.base = f'http://127.0.0.1:{api.server_address[1]}/game'
    identity = login_account(store, '76561197960278073')
    token = backend.token(identity['account_id'])
    threading.Thread(target=api.serve_forever, daemon=True).start()
    records, workers = [], []
    stopped = threading.Event()
    closing = threading.Event()
    lock = threading.RLock()
    definition = None
    peer_definitions = {}
    relay = None
    relays = {}
    source_script = None
    started = time.monotonic()
    output = (lab / 'events.jsonl').open('w', encoding='utf-8')
    checkpoint_writer = None
    checkpoint_epoch = None
    last_checkpoint_tick = -1
    game_configs = {}

    def log(role, payload):
        with lock:
            records.append((role, payload))
            output.write(json.dumps({'elapsed': round(time.monotonic() - started, 3), 'role': role, **payload}) + '\n')
            output.flush()
        if payload.get('event') in ('authority-probe-error', 'receiver-created', 'receiver-view-ready',
                                    'receiver-progress', 'receiver-stalled', 'stream-view-ready'):
            print(json.dumps({'role': role, **payload}), flush=True)

    def launch(role, shared=None, slot=0):
        nonlocal definition, relay, source_script
        config = {'endpoint': backend.base, 'token': token, 'saveCache': str(lab / role / 'save-cache'),
                  'sharedDefinition': shared, 'replicas': 1, 'player': True, 'viewAuthority': True,
                  'authorityOnly': True, 'crossSource': role == 'source', 'stopTick': 2147480000, 'bindView': False,
                  'syntheticMovement': not interactive}
        config['playerSlot'] = slot
        if role == 'source' and resumed_checkpoint is not None:
            config.update(restoreCheckpoint=True, checkpointTick=resumed_checkpoint.snapshot.tick,
                          checkpointChecksum=format(resumed_checkpoint.snapshot.checksum, 'x'),
                          sharedDefinition=resumed_checkpoint.metadata['definition'],
                          peerDefinitions=resumed_checkpoint.metadata['peers'], viewAuthority=False)
        (lab / role).mkdir()
        injected = 'const probeConfig = ' + json.dumps(config) + ';\n'
        names = ['bootstrap.js', 'runtime.js', 'input.js', 'stream.js' if role == 'source' else 'receiver.js']
        for name in names:
            injected += (ROOT / 'tools/authority_probe' / name).read_text(encoding='utf-8') + '\n'
        process_id = device.spawn([str(game / 'NoRestForTheWicked.exe'), '-logFile', str(lab / role / 'game.log'),
                                   '-screen-width', '640', '-screen-height', '360', '-screen-fullscreen', '0'],
                                  cwd=str(game), stdio='pipe')
        worker = {'role': role, 'pid': process_id, 'session': None}
        workers.append(worker)
        session = device.attach(process_id)
        worker['session'] = session
        session.on('detached', lambda *_: stopped.set())
        script = session.create_script(injected)

        def receive(message, data):
            nonlocal definition, checkpoint_writer, checkpoint_epoch, last_checkpoint_tick
            if closing.is_set():
                return
            if message.get('type') == 'error':
                log(role, {'event': 'authority-probe-error', 'error': message.get('description'), 'stack': message.get('stack')})
                stopped.set()
                return
            payload = message.get('payload', {})
            log(role, payload)
            try:
                if payload.get('event') == 'authority-probe-error':
                    stopped.set()
                elif role == 'source' and payload.get('event') == 'authority-definition':
                    definition = payload['definition']
                    checkpoint_epoch = checkpoint_store.begin_epoch(definition['realm'], checkpoint_store.generation(definition['realm']))
                    checkpoint_writer = CheckpointWriter(checkpoint_store, checkpoint_epoch, build)
                elif role == 'source' and payload.get('event') in ('authority-runtime-config', 'authority-session-config') and data is not None:
                    game_configs[payload['event']] = base64.b64encode(data).decode('ascii')
                elif role == 'source' and payload.get('event') == 'authority-peer-definition':
                    peer_definitions[payload['slot']] = payload['definition']
                elif role == 'source' and payload.get('event') == 'stream-snapshot' and data is not None:
                    for connection in list(relays.values()):
                        connection.publish(payload, data)
                    if checkpoint_writer is not None and len(game_configs) == 2 and payload['tick'] >= last_checkpoint_tick + 60:
                        last_checkpoint_tick = payload['tick']
                        checkpoint_writer.offer(Snapshot(payload['tick'], int(payload['checksum'], 16), bytes(data)),
                                                {**payload['bootstrap'], 'configs': dict(game_configs), 'fingerprints': fingerprints})
                elif role != 'source' and payload.get('event') == 'receiver-input' and data is not None:
                    relays[role].input(payload, data)
                elif role != 'source' and payload.get('event') == 'receiver-command' and data is not None:
                    relays[role].command(payload, data)
                elif role != 'source' and payload.get('event') == 'receiver-state-verified':
                    relays[role].acknowledge(payload['tick'])
                elif role != 'source' and payload.get('event') == 'receiver-view-ready':
                    if receivers == 1 and not interactive:
                        relay.arm_fault(payload['tick'])
            except Exception as error:
                log(role, {'event': 'authority-probe-error', 'error': str(error)})
                stopped.set()

        script.on('message', receive)
        script.load()
        if role == 'source':
            source_script = script
            relay = CrossProcessRelay(script, inject_fault=not interactive)
            relays['receiver'] = relay
        else:
            if role not in relays:
                relays[role] = CrossProcessRelay(source_script, slot, inject_fault=False)
            relays[role].receiver = script
        if role == 'source' and resumed_checkpoint is not None:
            script.post({'type': 'restore-frame'}, resumed_checkpoint.snapshot.data)
            script.post({'type': 'restore-runtime'}, base64.b64decode(resumed_checkpoint.metadata['configs']['authority-runtime-config'], validate=True))
            script.post({'type': 'restore-session'}, base64.b64decode(resumed_checkpoint.metadata['configs']['authority-session-config'], validate=True))
        device.resume(process_id)
        print(json.dumps({'event': 'process-started', 'role': role, 'pid': process_id, 'output': str(lab)}), flush=True)
        return worker

    try:
        launch('source')
        receiver_started = False
        second_requested = second_started = False
        while (seconds == 0 or time.monotonic() - started < seconds) and not stopped.wait(0.5):
            if definition is not None and not receiver_started:
                receiver_started = True
                launch('receiver', definition)
            if receivers == 2:
                with lock:
                    source_ready = any(role == 'source' and p.get('event') in ('stream-view-ready', 'authority-checkpoint-restored') for role, p in records)
                    ready_roles = {role for role, p in records if p.get('event') == 'receiver-view-ready'}
                if source_ready and not second_requested and 1 not in peer_definitions:
                    second_requested = True
                    source_script.post({'type': 'authority-add-player', 'payload': {'slot': 1, 'character': secrets.token_hex(16)}})
                if 1 in peer_definitions and not second_started:
                    second_started = True
                    launch('receiver2', peer_definitions[1], slot=1)
                if not interactive and ready_roles >= {'receiver', 'receiver2'}:
                    relay.arm_fault(relay.report()['latest_source_tick'])
            if relay is not None:
                transport = relay.report()
                if transport['errors']:
                    break
                if transport['fault_end'] is not None and transport['latest_source_tick'] >= transport['fault_end'] + 360:
                    break
    except KeyboardInterrupt:
        if not interactive:
            log('launcher', {'event': 'authority-probe-error', 'error': 'Probe interrupted'})
    except Exception as error:
        log('launcher', {'event': 'authority-probe-error', 'error': str(error)})
    finally:
        closing.set()
        if checkpoint_writer is not None:
            try:
                checkpoint_writer.close()
            except Exception as error:
                log('checkpoint', {'event': 'authority-probe-error', 'error': str(error)})
        connections = {role: connection.close() for role, connection in relays.items()}
        transport = connections.get('receiver', {})
        for worker in reversed(workers):
            try:
                device.kill(worker['pid'])
            except (frida.ProcessNotFoundError, frida.InvalidOperationError):
                pass
            if worker['session'] is not None:
                try:
                    worker['session'].detach()
                except frida.InvalidOperationError:
                    pass
        api.shutdown()
        api.server_close()
        output.close()
    report = summarize(records, transport)
    checkpoint = checkpoint_store.load(checkpoint_epoch.realm, build) if checkpoint_epoch is not None else None
    report.update(checkpoint_reopened=checkpoint is not None, checkpoint_tick=checkpoint.snapshot.tick if checkpoint else None)
    if checkpoint is None:
        report['probe_passed'] = False
    report['connections'] = connections
    if interactive:
        ready_roles = {role for role, p in records if p.get('event') == 'receiver-view-ready'}
        expected_roles = {'receiver'} | ({'receiver2'} if receivers == 2 else set())
        passed = expected_roles <= ready_roles and checkpoint is not None and not report['probe_errors']
        report.update(probe_passed=passed, networked_state_sync_verified=False,
                      local_session_ready=passed, fault_injection_enabled=False,
                      mode='interactive local development session', scope='isolated local trial realm; no legacy save migration')
    if receivers == 2:
        fault_start, fault_end = transport.get('fault_start'), transport.get('fault_end')
        source_states = {p['tick']: p for role, p in records if role == 'source' and p.get('event') == 'stream-snapshot'}
        healthy = [p for role, p in records if role == 'receiver2' and p.get('event') == 'receiver-state-verified'
                   and p.get('tick') in source_states and p.get('checksum') == source_states[p['tick']].get('checksum')]
        healthy_fault = [p for p in healthy if fault_start is not None and fault_start <= p['tick'] < fault_end]
        healthy_bound = any(role == 'receiver2' and p.get('event') == 'receiver-view-ready' for role, p in records)
        healthy_connection = connections.get('receiver2', {})
        report.update(healthy_client_verified_updates=len(healthy), healthy_client_updates_during_other_fault=len(healthy_fault),
                      healthy_client_view_bound=healthy_bound,
                      scope='three isolated processes, two synthetic players, loopback TCP and generated movement; no multiplayer saves')
        if not interactive:
            report['probe_passed'] = report['networked_state_sync_verified'] = bool(
                report['probe_passed'] and healthy_bound and len(healthy_fault) >= 5
                and healthy_connection.get('inputs_forwarded', 0) >= 120 and not healthy_connection.get('errors'))
    game_errors = []
    for worker in workers:
        path = lab / worker['role'] / 'game.log'
        if path.exists():
            game_errors += [worker['role'] + ': ' + line for line in path.read_text(encoding='utf-8', errors='replace').splitlines() if '|X|' in line or '|E|' in line][:5]
    report.update(game_exceptions=game_errors, game_fingerprints=fingerprints,
                  duration_seconds=round(time.monotonic() - started, 2), process_ids=[w['pid'] for w in workers])
    if game_errors or len({w['pid'] for w in workers}) != receivers + 1:
        report['probe_passed'] = report['networked_state_sync_verified'] = False
    (lab / 'result.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report, lab


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-directory', type=Path)
    parser.add_argument('--seconds', type=int, default=180)
    parser.add_argument('--receivers', type=int, choices=(1, 2), default=1)
    args = parser.parse_args(argv)
    try:
        game = args.game_directory or Path(json.loads((ROOT / 'workspace.local.json').read_text(encoding='utf-8-sig'))['game_directory'])
        if not 60 <= args.seconds <= 600:
            raise ValueError('--seconds must be between 60 and 600')
        report, lab = run(game.resolve(), verify_installation(game), args.seconds, args.receivers)
        print(json.dumps({'result': report, 'output': str(lab)}), flush=True)
        return 0 if report['probe_passed'] else 1
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
