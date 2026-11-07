"""Restart a new isolated engine from a checkpoint read back from durable storage."""
import base64
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import json
import secrets
import threading
import time


def summarize_restart(records, checkpoint):
    matches = [p for p in records if p.get('event') == 'restart-checksum-verified'
               and p.get('tick') == checkpoint.snapshot.tick
               and p.get('checksum') == format(checkpoint.snapshot.checksum, 'x')]
    progress = [p for p in records if p.get('event') == 'restart-progress']
    errors = [p.get('error') for p in records if p.get('event') == 'authority-probe-error']
    resumed = bool(len(progress) >= 3 and progress[-1]['tick'] >= checkpoint.snapshot.tick + 600
                   and all(b['tick'] > a['tick'] for a, b in zip(progress, progress[1:])))
    complete = any(p.get('event') == 'restart-complete' for p in records)
    return {'probe_passed': bool(matches and resumed and complete and not errors),
            'checkpoint_tick': checkpoint.snapshot.tick, 'restored_checksum_verified': bool(matches),
            'resumed_after_restart': resumed,
            'last_tick': progress[-1]['tick'] if progress else None,
            'initial_players': matches[0].get('players') if matches else None,
            'probe_errors': errors}


def verify_restart(game, fingerprints, checkpoint, lab, root, seconds=120, checkpoint_store=None, realm=None, build=None):
    import frida
    from server import Store
    from game_backend import GameBackend, GameHandler
    from launcher_auth import login_account
    from authoritative_checkpoints import CheckpointWriter
    from authoritative_state import Snapshot

    if checkpoint.metadata.get('fingerprints') != fingerprints:
        raise ValueError('Cannot restart a checkpoint with a different game build')
    device = frida.get_local_device()
    if any(p.name.lower() == 'norestforthewicked.exe' for p in device.enumerate_processes()):
        raise ValueError('Another game is running; restart verification will not attach to it')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = lab / f'restart-{stamp}-{secrets.token_hex(3)}'
    target.mkdir()
    store = Store(target / 'backend.sqlite3', secrets.token_urlsafe(32))
    backend = GameBackend(store, 'http://127.0.0.1', transport_experiment=True)

    class QuietHandler(GameHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
    server.daemon_threads = True
    server.store, server.game = store, backend
    backend.base = f'http://127.0.0.1:{server.server_address[1]}/game'
    identity = login_account(store, '76561197960278073')
    config = {'endpoint': backend.base, 'token': backend.token(identity['account_id']),
              'saveCache': str(target / 'save-cache'), 'sharedDefinition': checkpoint.metadata['definition'],
              'checkpointTick': checkpoint.snapshot.tick, 'checkpointChecksum': format(checkpoint.snapshot.checksum, 'x')}
    source = 'const probeConfig = ' + json.dumps(config) + ';\n'
    for name in ('bootstrap.js', 'runtime.js', 'restart.js'):
        source += (root / 'tools/authority_probe' / name).read_text(encoding='utf-8') + '\n'
    stopped = threading.Event()
    closing = threading.Event()
    records = []
    started = time.monotonic()
    process_id = session = None
    writer = None
    if checkpoint_store is not None:
        epoch = checkpoint_store.begin_epoch(realm, checkpoint_store.generation(realm))
        writer = CheckpointWriter(checkpoint_store, epoch, build)
    output = (target / 'events.jsonl').open('w', encoding='utf-8')
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def receive(message, data):
        if closing.is_set():
            return
        payload = message.get('payload', {})
        if message.get('type') == 'error':
            payload = {'event': 'authority-probe-error', 'error': message.get('description'), 'stack': message.get('stack')}
        records.append(payload)
        output.write(json.dumps(payload) + '\n')
        output.flush()
        if payload.get('event') == 'restart-checkpoint' and data is not None and writer is not None:
            try:
                writer.offer(Snapshot(payload['tick'], int(payload['checksum'], 16), bytes(data)), checkpoint.metadata)
            except Exception as error:
                records.append({'event': 'authority-probe-error', 'error': str(error)})
                stopped.set()
        if payload.get('event') in ('restart-checksum-verified', 'restart-authority-running',
                                    'restart-progress', 'restart-complete', 'authority-probe-error'):
            print(json.dumps({'role': 'restart', **payload}), flush=True)
        if payload.get('event') in ('authority-probe-error', 'restart-complete'):
            stopped.set()

    try:
        process_id = device.spawn([str(game / 'NoRestForTheWicked.exe'), '-logFile', str(target / 'game.log'),
                                   '-screen-width', '640', '-screen-height', '360', '-screen-fullscreen', '0'],
                                  cwd=str(game), stdio='pipe')
        session = device.attach(process_id)
        session.on('detached', lambda *_: stopped.set())
        script = session.create_script(source)
        script.on('message', receive)
        script.load()
        script.post({'type': 'restore-frame'}, checkpoint.snapshot.data)
        script.post({'type': 'restore-runtime'}, base64.b64decode(checkpoint.metadata['configs']['authority-runtime-config'], validate=True))
        script.post({'type': 'restore-session'}, base64.b64decode(checkpoint.metadata['configs']['authority-session-config'], validate=True))
        device.resume(process_id)
        print(json.dumps({'event': 'restart-started', 'pid': process_id, 'tick': checkpoint.snapshot.tick}), flush=True)
        while time.monotonic() - started < seconds and not stopped.wait(0.5):
            pass
    finally:
        closing.set()
        if writer is not None:
            try:
                writer.close()
            except Exception as error:
                records.append({'event': 'authority-probe-error', 'error': str(error)})
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
        server.shutdown()
        server.server_close()
        output.close()
    report = summarize_restart(records, checkpoint)
    if checkpoint_store is not None:
        saved = checkpoint_store.load(realm, build)
        report['saved_again_after_restart'] = saved is not None and saved.snapshot.tick >= checkpoint.snapshot.tick + 600
        if not report['saved_again_after_restart']:
            report['probe_passed'] = False
    log = target / 'game.log'
    errors = [line for line in log.read_text(encoding='utf-8', errors='replace').splitlines()
              if '|E|' in line or '|X|' in line] if log.exists() else []
    report.update(game_errors=errors[:5], process_id=process_id, duration_seconds=round(time.monotonic() - started, 2))
    if errors:
        report['probe_passed'] = False
    (target / 'result.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report
