"""Launch the test client with a hash-checked in-memory backend redirect."""
import argparse
import hashlib
import json
import sys
import threading
import signal
import time
import platform
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
compat_runtime=ROOT/'tools/frida-compat'
sys.path.insert(0, str(compat_runtime if (compat_runtime/'frida').is_dir() else ROOT/'tools/python-deps'))
from client.client_identity import cache_directory, game_arguments


def main():
    if hasattr(signal,'SIGBREAK'):
        def on_break(*args):raise KeyboardInterrupt
        signal.signal(signal.SIGBREAK,on_break)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint')
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=0)
    parser.add_argument('--pid', type=int)
    parser.add_argument('--cancel-file', type=Path)
    parser.add_argument('--skip-prologue-entry-test', action='store_true', help='Diagnostic only: skip the local prologue branch when requesting a realm')
    parser.add_argument('--transport-probe', action='store_true', help='Direct Photon TCP to the local research listener; not a playable server')
    args = parser.parse_args()
    import frida
    profile = json.loads(args.profile.read_text(encoding='utf-8'))
    args.endpoint = args.endpoint or profile['endpoint']
    if urlsplit(args.endpoint).hostname not in ('127.0.0.1', 'localhost'):
        raise SystemExit('Use a loopback endpoint or SSH tunnel for this experimental adapter')
    fingerprint = json.loads((ROOT / 'research/client-fingerprint.json').read_text())
    assembly = ROOT.parent / 'GameAssembly.dll'
    with assembly.open('rb') as game_file:
        if hashlib.file_digest(game_file, 'sha256').hexdigest() != fingerprint['GameAssembly.dll']['sha256']:
            raise SystemExit('Unsupported GameAssembly.dll; refusing to install build-specific hooks')
    diagnostics={'frida_version':frida.__version__,'python_version':platform.python_version(),
                 'os':platform.system(),'architecture':platform.machine()}
    if hasattr(sys,'getwindowsversion'):
        win=sys.getwindowsversion()
        diagnostics['windows_version']={'major':win.major,'minor':win.minor,'build':win.build}
    (ROOT/'logs').mkdir(exist_ok=True)
    (ROOT/'logs/runtime-info.json').write_text(json.dumps(diagnostics,indent=2),encoding='utf-8')
    print('Adapter runtime: Frida '+frida.__version__+'; '+platform.system()+' '+str(diagnostics.get('windows_version',{}).get('build','')),flush=True)
    source = (ROOT / 'client/redirect.js').read_text(encoding='utf-8').replace('__ENDPOINT__', json.dumps(args.endpoint.rstrip('/'))).replace('__PRIVATE_TOKEN__', json.dumps(profile['token'])).replace('__SKIP_PROLOGUE__', json.dumps(args.skip_prologue_entry_test))
    source = source.replace('__TRANSPORT_PROBE__', json.dumps(args.transport_probe))
    source = source.replace('__ACCOUNT_NAME__', json.dumps('' if profile.get('steam_id') else profile.get('name',''),ensure_ascii=True))
    source = source.replace('__STEAM_ID__', json.dumps(profile.get('steam_id')))
    cache=cache_directory(ROOT,profile)
    if cache is not None:cache.mkdir(parents=True,exist_ok=True)
    source = source.replace('__SAVE_CACHE__', json.dumps(str(cache.resolve()) if cache is not None else None,ensure_ascii=True))
    if args.transport_probe:
        # Use an IIFE so the independently maintained repair has its own scope.
        source += '\n(function(){\n' + (ROOT / 'client/ping_fix.js').read_text(encoding='utf-8') + '\n})();\n'
    # Retain the previous run's evidence instead of silently truncating it.
    evidence=ROOT/'research'
    old=[evidence/name for name in ('client-adapter.jsonl','test-player.log') if (evidence/name).is_file()]
    if old:
        archived=evidence/'runs'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        archived.mkdir(parents=True,exist_ok=True)
        import shutil
        for file in old:shutil.copy2(file,archived/file.name)
    logpath = evidence / 'client-adapter.jsonl'
    ended = threading.Event()
    identity_error=threading.Event()
    with logpath.open('w', encoding='utf-8') as log:
        def message(msg, data):
            payload = msg.get('payload', msg)
            if isinstance(payload,dict) and payload.get('event')=='steam-identity-mismatch':identity_error.set()
            if isinstance(payload,dict):payload={'time':datetime.now(timezone.utc).isoformat(),**payload}
            line = json.dumps(payload, ensure_ascii=False)
            print(line, flush=True)
            log.write(line + '\n'); log.flush()
        device = frida.get_local_device()
        executable = str(ROOT.parent / 'NoRestForTheWicked.exe')
        pid = args.pid or device.spawn(game_arguments(executable,ROOT/'research/test-player.log'),
                                      cwd=str(ROOT.parent),stdio='pipe')
        session = device.attach(pid)
        def detached(reason, crash=None):
            event={'event':'process-detached','reason':reason,'crash':None}
            if crash is not None:
                event['crash']={'summary':getattr(crash,'summary',str(crash)),
                                'report':getattr(crash,'report',None)}
            message({'payload':event},None)
            ended.set()
        session.on('detached', detached)
        script = session.create_script(source)
        script.on('message', message)
        script.load()
        if not args.pid:
            device.resume(pid)
        message({'payload':{'event':'launched','pid':pid}},None)
        try:
            deadline=time.monotonic()+args.seconds if args.seconds else None
            while not ended.wait(.25):
                if identity_error.is_set():break
                if deadline is not None and time.monotonic()>=deadline:break
                if args.cancel_file and args.cancel_file.exists():break
        except KeyboardInterrupt:
            pass
        finally:
            # Do not let a running test silently fall back to official endpoints.
            if not ended.is_set():
                device.kill(pid)
    if identity_error.is_set():raise SystemExit('Game Steam identity differs from the server account; stopped before continuing')


if __name__ == '__main__':
    main()
