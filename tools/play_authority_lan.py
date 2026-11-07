"""Experimental two-PC host-authority trial, using one installed game per PC.

This uses an isolated trial realm and the development Frida adapter. The normal
launcher and the shipping Photon server are separate. Use on a trusted LAN.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'server'))

from authoritative_state import Snapshot
from authoritative_checkpoints import CheckpointStore, CheckpointWriter
from tools.authority_lan_protocol import (LanPeer, encode, decode, KIND_CONTROL,
                                          KIND_STATE, KIND_INPUT, KIND_COMMAND)
from tools.probe_authority_runtime import verify_installation


def proof(code, nonce, build):
    return hmac.new(code.encode(), ('wicked-authority-lan-v1:' + nonce + ':' + build).encode(), hashlib.sha256).hexdigest()


def checked_sequence(meta, data, last, *, command=False):
    sequence = meta.get('sequence')
    if type(sequence) is not int or not last < sequence < 2**32:
        raise ValueError('Invalid or repeated input sequence')
    if command:
        if not 0 < len(data) <= 1024 * 1024:
            raise ValueError('Invalid command length')
    elif len(data) != 47 or type(meta.get('tick')) is not int or not 0 <= meta['tick'] < 2**31:
        raise ValueError('Invalid native input')
    return sequence


class Trial:
    def __init__(self, args):
        self.args = args
        local = json.loads((ROOT / 'workspace.local.json').read_text(encoding='utf-8-sig')) if not args.game_directory else {}
        self.game = (args.game_directory or Path(local['game_directory'])).resolve()
        self.fingerprints = verify_installation(self.game)
        self.build = hashlib.sha256(json.dumps(self.fingerprints, sort_keys=True).encode()).hexdigest()
        self.directory = args.state_directory.resolve()
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        self.lab = self.directory / 'runs' / (args.mode + '-' + stamp + '-' + secrets.token_hex(3))
        self.lab.mkdir(parents=True)
        self.lock = threading.RLock()
        self.log_lock = threading.Lock()
        self.output = (self.lab / 'events.jsonl').open('w', encoding='utf-8')
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.script = self.session = self.pid = self.peer = None
        self.listener = self.api = self.writer = None
        self.definition = self.peer_definition = None
        self.configs = {}
        self.latest = None
        self.inflight = None
        self.last_sent_tick = -1
        self.last_checkpoint = -1
        self.input_sequence = self.command_sequence = 0
        self.counts = dict(states_sent=0, states_verified=0, inputs_received=0, commands_received=0)
        self.errors = []
        self.started = time.monotonic()
        self.store = self.epoch = None
        self.checkpoint = None
        self.threads = []

    def log(self, event, **values):
        record = dict(event=event, **values)
        with self.log_lock:
            self.output.write(json.dumps(dict(elapsed=round(time.monotonic() - self.started, 3), **record)) + '\n')
            self.output.flush()
        if event in ('authority-probe-error', 'stream-view-ready', 'receiver-view-ready',
                     'lan-listening', 'lan-connected', 'lan-disconnected', 'game-started',
                     'authority-peer-definition', 'authority-checkpoint-restored',
                     'lan-peer-rejected'):
            print(json.dumps(record, ensure_ascii=False), flush=True)

    def fail(self, error):
        self.errors.append(str(error))
        self.log('authority-probe-error', error=str(error))
        self.stop.set()

    def backend(self):
        from server import Store
        from game_backend import GameBackend, GameHandler
        from launcher_auth import login_account

        class QuietHandler(GameHandler):
            def log_message(self, *_):
                pass

        store = Store(self.lab / 'backend.sqlite3', secrets.token_urlsafe(32))
        backend = GameBackend(store, 'http://127.0.0.1', transport_experiment=True)
        self.api = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.api.daemon_threads = True
        self.api.store, self.api.game = store, backend
        backend.base = f'http://127.0.0.1:{self.api.server_address[1]}/game'
        identity = login_account(store, '76561197960278073')
        self.spawn_thread(self.api.serve_forever)
        return backend.base, backend.token(identity['account_id'])

    def spawn_thread(self, target):
        thread = threading.Thread(target=target, daemon=True)
        self.threads.append(thread)
        thread.start()

    def launch(self, shared=None):
        import frida
        device = frida.get_local_device()
        if not self.args.allow_local_diagnostic and any(p.name.lower() == 'norestforthewicked.exe' for p in device.enumerate_processes()):
            raise ValueError('A game is already running; close it before this isolated trial')
        endpoint, token = self.backend()
        host = self.args.mode == 'host'
        config = dict(endpoint=endpoint, token=token, saveCache=str(self.lab / 'save-cache'),
                      player=True, viewAuthority=host, authorityOnly=True, crossSource=host,
                      hostPlayer=host, stopTick=2147480000, bindView=False, playerSlot=1,
                      sharedDefinition=shared, syntheticMovement=self.args.synthetic_movement)
        if self.checkpoint:
            config.update(restoreCheckpoint=True, checkpointTick=self.checkpoint.snapshot.tick,
                          checkpointChecksum=format(self.checkpoint.snapshot.checksum, 'x'),
                          sharedDefinition=self.checkpoint.metadata['definition'],
                          peerDefinitions=self.checkpoint.metadata['peers'])
        source = 'const probeConfig = ' + json.dumps(config) + ';\n'
        for name in ('bootstrap.js', 'runtime.js', 'input.js', 'stream.js' if host else 'receiver.js'):
            source += (ROOT / 'tools/authority_probe' / name).read_text(encoding='utf-8') + '\n'
        self.pid = device.spawn([str(self.game / 'NoRestForTheWicked.exe'), '-logFile', str(self.lab / 'game.log'),
                                 '-screen-width', '1280', '-screen-height', '720', '-screen-fullscreen', '0'],
                                cwd=str(self.game), stdio='pipe')
        self.session = device.attach(self.pid)
        self.session.on('detached', lambda *_: self.stop.set())
        self.script = self.session.create_script(source)
        self.script.on('message', self.on_message)
        self.script.load()
        if self.checkpoint:
            import base64
            self.script.post({'type': 'restore-frame'}, self.checkpoint.snapshot.data)
            self.script.post({'type': 'restore-runtime'}, base64.b64decode(self.checkpoint.metadata['configs']['authority-runtime-config'], validate=True))
            self.script.post({'type': 'restore-session'}, base64.b64decode(self.checkpoint.metadata['configs']['authority-session-config'], validate=True))
        device.resume(self.pid)
        self.log('game-started', mode=self.args.mode, pid=self.pid, output=str(self.lab))

    def on_message(self, message, data):
        if self.stop.is_set():
            return
        if message.get('type') == 'error':
            self.fail(message.get('description', 'Injected script failed'))
            return
        meta = message.get('payload', {})
        event = meta.get('event')
        if not event:
            return
        self.log(event, **{key: value for key, value in meta.items() if key != 'event'})
        try:
            if event == 'authority-probe-error':
                self.fail(meta.get('error', 'Game probe failed'))
            elif event == 'authority-definition':
                self.definition = meta['definition']
                self.epoch = self.store.begin_epoch(self.definition['realm'], self.store.generation(self.definition['realm']))
                self.writer = CheckpointWriter(self.store, self.epoch, self.build)
            elif event in ('authority-runtime-config', 'authority-session-config') and data is not None:
                import base64
                self.configs[event] = base64.b64encode(data).decode('ascii')
            elif event == 'authority-peer-definition' and meta['slot'] == 1:
                self.peer_definition = meta['definition']
            elif event in ('stream-view-ready', 'receiver-view-ready'):
                self.ready.set()
            elif event == 'stream-snapshot' and data is not None:
                snapshot = Snapshot(meta['tick'], int(meta['checksum'], 16), bytes(data))
                with self.lock:
                    self.latest = snapshot
                    self.dispatch_state()
                if self.writer and len(self.configs) == 2 and snapshot.tick >= self.last_checkpoint + 60:
                    self.last_checkpoint = snapshot.tick
                    self.writer.offer(snapshot, dict(**meta['bootstrap'], configs=dict(self.configs), fingerprints=self.fingerprints))
            elif event == 'receiver-input' and data is not None:
                self.peer.send_input(meta, data)
            elif event == 'receiver-command' and data is not None:
                self.peer.send_command(meta, data)
            elif event == 'receiver-state-verified':
                self.counts['states_verified'] += 1
                self.peer.send_control(dict(event='ack', tick=meta['tick'], checksum=meta['checksum']))
        except Exception as error:
            self.fail(error)

    def dispatch_state(self):
        # Caller holds the host lock. Only one state is in flight per connection.
        if (self.peer is None or self.peer.closed.is_set() or self.inflight is not None or
                self.latest is None or self.latest.tick <= self.last_sent_tick):
            return
        snapshot = self.latest
        self.inflight = snapshot
        self.last_sent_tick = snapshot.tick
        self.peer.send_state(dict(tick=snapshot.tick, checksum=format(snapshot.checksum, 'x'),
                                  digest=snapshot.digest.hex()), snapshot.data)
        self.counts['states_sent'] += 1

    def host_packet(self, kind, meta, data, sequences):
        with self.lock:
            if kind == KIND_CONTROL and meta.get('event') == 'ack':
                if self.inflight is None or meta.get('tick') != self.inflight.tick or meta.get('checksum') != format(self.inflight.checksum, 'x'):
                    raise ValueError('Unexpected state acknowledgement')
                self.counts['states_verified'] += 1
                self.inflight = None
                self.dispatch_state()
            elif kind in (KIND_INPUT, KIND_COMMAND):
                command = kind == KIND_COMMAND
                key = 'command' if command else 'input'
                sequences[key] = checked_sequence(meta, data, sequences[key], command=command)
                if command:
                    sequence, self.command_sequence = self.command_sequence, self.command_sequence + 1
                    self.script.post({'type': 'authority-command', 'payload': dict(slot=1, sequence=sequence)}, data)
                    self.counts['commands_received'] += 1
                else:
                    if self.latest and meta['tick'] > self.latest.tick + 600:
                        raise ValueError('Input tick is too far in the future')
                    sequence, self.input_sequence = self.input_sequence, self.input_sequence + 1
                    self.script.post({'type': 'authority-input', 'payload': dict(slot=1, sequence=sequence, tick=meta['tick'])}, data)
                    self.counts['inputs_received'] += 1
            else:
                raise ValueError('Guest may send only input, commands and acknowledgements')

    def accept_guest(self, connection):
        peer = None
        try:
            connection.settimeout(15)
            nonce = secrets.token_hex(32)
            connection.sendall(encode(KIND_CONTROL, dict(event='challenge', protocol=1, build=self.build, nonce=nonce)))
            kind, meta, data = decode(connection)
            if kind != KIND_CONTROL or data or meta.get('event') != 'join' or meta.get('build') != self.build or not hmac.compare_digest(str(meta.get('proof', '')), proof(self.code, nonce, self.build)):
                raise ValueError('Build or join code does not match')
            deadline = time.monotonic() + 120
            while not self.ready.is_set() and not self.stop.wait(0.1) and time.monotonic() < deadline:
                pass
            if not self.ready.is_set():
                raise TimeoutError('Host player view is not ready')
            if self.peer_definition is None:
                self.script.post({'type': 'authority-add-player', 'payload': dict(slot=1, character=secrets.token_hex(16))})
                deadline = time.monotonic() + 20
                while self.peer_definition is None and not self.stop.wait(0.05) and time.monotonic() < deadline:
                    pass
            if self.peer_definition is None:
                raise TimeoutError('Authority did not insert guest player')
            connection.sendall(encode(KIND_CONTROL, dict(event='welcome', protocol=1, build=self.build,
                                                        slot=1, definition=self.peer_definition)))
            peer = LanPeer(connection, 'guest')
            sequences = {'input': -1, 'command': -1}
            with self.lock:
                self.peer, self.inflight, self.last_sent_tick = peer, None, -1
                self.dispatch_state()
            self.log('lan-connected', slot=1)
            peer.receive_loop(lambda kind, meta, data: self.host_packet(kind, meta, data, sequences))
            if peer.error:
                self.log('lan-peer-rejected', reason=peer.error)
        except Exception as error:
            self.log('lan-peer-rejected', reason=str(error))
        finally:
            if peer:
                peer.close()
            else:
                connection.close()
            with self.lock:
                self.peer, self.inflight = None, None
            self.log('lan-disconnected', slot=1)

    def host(self):
        self.store = CheckpointStore(self.directory / 'checkpoints.sqlite3')
        realms = self.store.realms()
        if len(realms) > 1:
            raise ValueError('Use one host realm per state directory')
        if realms:
            self.checkpoint = self.store.load(realms[0], self.build)
        code_path = self.directory / 'join-code.txt'
        if not code_path.exists():
            code_path.write_text(secrets.token_urlsafe(24) + '\n', encoding='utf-8')
        self.code = code_path.read_text(encoding='utf-8').strip()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((self.args.listen, self.args.port))
        self.listener.listen(2)
        self.listener.settimeout(0.5)
        self.launch()
        self.log('lan-listening', address=self.listener.getsockname(), code_file=str(code_path))
        def accept_loop():
            while not self.stop.is_set():
                try:
                    connection, _ = self.listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                # A single guest belongs to fixed slot 1, including reconnects.
                if self.peer is not None:
                    connection.close()
                    continue
                self.accept_guest(connection)
        self.spawn_thread(accept_loop)

    def guest(self):
        code = self.args.code_file.read_text(encoding='utf-8').strip() if self.args.code_file else getpass.getpass('房主的加入码: ').strip()
        connection = socket.create_connection((self.args.host, self.args.port), timeout=15)
        try:
            kind, meta, data = decode(connection)
            if kind != KIND_CONTROL or data or meta.get('event') != 'challenge' or meta.get('protocol') != 1 or meta.get('build') != self.build:
                raise ValueError('Unsupported LAN authority protocol or game build')
            connection.sendall(encode(KIND_CONTROL, dict(event='join', build=self.build,
                                                        proof=proof(code, meta['nonce'], self.build))))
            connection.settimeout(150)
            kind, meta, data = decode(connection)
            if (kind != KIND_CONTROL or data or meta.get('event') != 'welcome'
                    or meta.get('slot') != 1 or meta.get('protocol') != 1 or meta.get('build') != self.build):
                raise ValueError('Host did not authorize guest slot 1')
            self.peer = LanPeer(connection, 'host')
            self.launch(meta['definition'])
            last_tick = -1
            def receive(kind, meta, data):
                nonlocal last_tick
                if kind != KIND_STATE:
                    raise ValueError('Host may send only complete simulation states')
                snapshot = Snapshot(meta['tick'], int(meta['checksum'], 16), bytes(data))
                if snapshot.digest.hex() != meta.get('digest'):
                    raise ValueError('State digest mismatch')
                if snapshot.tick <= last_tick:
                    raise ValueError('State tick did not advance')
                last_tick = snapshot.tick
                self.script.post({'type': 'authoritative-state', 'payload': dict(tick=snapshot.tick, checksum=meta['checksum'])}, snapshot.data)
            self.spawn_thread(lambda: self.peer.receive_loop(receive))
            self.log('lan-connected', slot=1)
        except BaseException:
            connection.close()
            raise

    def run(self):
        try:
            if self.args.mode == 'host':
                self.host()
            else:
                self.guest()
            while not self.stop.wait(0.5):
                if self.args.seconds and time.monotonic() - self.started >= self.args.seconds:
                    break
                if self.args.mode == 'guest' and self.peer.closed.is_set():
                    break
        except KeyboardInterrupt:
            pass
        except Exception as error:
            self.fail(error)
        finally:
            if self.args.mode == 'guest' and self.peer and self.peer.error:
                self.errors.append(self.peer.error)
            self.close()
        return 1 if self.errors else 0

    def close(self):
        self.stop.set()
        if self.listener:
            self.listener.close()
        if self.peer:
            self.peer.close()
        if self.writer:
            try:
                self.writer.close()
            except Exception as error:
                self.errors.append(str(error))
        if self.pid:
            import frida
            try:
                frida.get_local_device().kill(self.pid)
            except (frida.ProcessNotFoundError, frida.InvalidOperationError):
                pass
        if self.session:
            try:
                self.session.detach()
            except Exception:
                pass
        if self.api:
            self.api.shutdown()
            self.api.server_close()
        for thread in self.threads:
            thread.join(timeout=2)
        report = dict(mode=self.args.mode, counts=self.counts, errors=self.errors,
                      host_view_ready=self.args.mode == 'host' and self.ready.is_set(),
                      player_view_ready=self.ready.is_set(), game_fingerprints=self.fingerprints,
                      physical_two_pc_verified=False, manual_gameplay_verified=False,
                      elapsed_seconds=round(time.monotonic() - self.started, 2))
        (self.lab / 'result.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        self.output.close()
        print(json.dumps(dict(output=str(self.lab), **report), ensure_ascii=False), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('host', 'guest'))
    parser.add_argument('--game-directory', type=Path)
    parser.add_argument('--state-directory', type=Path, default=ROOT / 'dev-artifacts/authority-lan')
    parser.add_argument('--listen', default='0.0.0.0')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18881)
    parser.add_argument('--code-file', type=Path, help='guest: read host join code from a local file')
    parser.add_argument('--seconds', type=int, default=0, help='0 runs until Ctrl+C or game exit')
    parser.add_argument('--synthetic-movement', action='store_true', help='diagnostic guest walking; omit for real keyboard/controller input')
    parser.add_argument('--allow-local-diagnostic', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return Trial(args).run()


if __name__ == '__main__':
    raise SystemExit(main())
