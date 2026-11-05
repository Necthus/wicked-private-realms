"""Prepare separate opt-in experimental packages; never upload or deploy them."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'v0.3.0-five-alpha.1'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stable-client', type=Path, required=True)
    args = parser.parse_args()
    output = ROOT / 'releases' / VERSION
    output.mkdir(parents=True, exist_ok=True)
    binaries = {name: (ROOT / 'native/dist-five' / name).read_bytes()
                for name in ('WickedNativeHost.exe', 'WickedAdapter.dll')}
    with zipfile.ZipFile(args.stable_client) as archive:
        payload = {name: archive.read(name) for name in archive.namelist() if not name.endswith('/')}
    original = json.loads(payload['wicked-client/native/BUILD-METADATA.json'])
    for name, expected in original['files'].items():
        if hashlib.sha256(payload['wicked-client/' + name]).hexdigest() != expected:
            raise ValueError('Stable binary hash mismatch: ' + name)
    for name, raw in binaries.items():
        payload['wicked-client/' + name] = raw
    payload['wicked-client/Start-Five-Player.cmd'] = (
        '@echo off\r\nsetlocal\r\nset "WICKED_EXPERIMENTAL_PLAYERS=5"\r\n'
        'start "" "%~dp0WickedLauncher.exe"\r\n').encode('ascii')
    payload['wicked-client/README.md'] = (ROOT / 'docs/FIVE-PLAYER-TEST.md').read_bytes()
    payload['wicked-client/FIVE-PLAYER-FEASIBILITY.md'] = (ROOT / 'docs/FIVE-PLAYER-FEASIBILITY.md').read_bytes()
    payload['wicked-client/data/launcher-settings.json'] = json.dumps(
        {'server': '', 'steam_id': '', 'steam_mode': 'gse'}, indent=2).encode()
    payload['wicked-client/native/BUILD-METADATA.json'] = json.dumps({
        'mode': 'native', 'version': VERSION, 'experimental': True, 'max_players': 5,
        'game_build': '29466', 'python_runtime_included': False, 'frida_runtime_included': False,
        'files': {name: hashlib.sha256(payload['wicked-client/' + name]).hexdigest()
                  for name in ('WickedLauncher.exe', 'WickedNativeHost.exe', 'WickedAdapter.dll')},
    }, indent=2).encode()
    client = output / ('Wicked-Private-Realms-Client-v29466-' + VERSION + '.zip')
    with zipfile.ZipFile(client, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, raw in sorted(payload.items()):
            if name.endswith(('.py', '.js', '.pem', '.key', '.sqlite3')):
                raise ValueError('Unexpected source/private file in client: ' + name)
            archive.writestr(name, raw)
    import sys
    sys.path.insert(0, str(ROOT / 'tools'))
    from build_server_release import build
    with tempfile.TemporaryDirectory(prefix='wicked-five-server-') as temporary:
        built = build(Path(temporary))
        with zipfile.ZipFile(built) as archive:
            server_payload = {name: archive.read(name) for name in archive.namelist()}
        settings = json.loads(server_payload['wicked-private-realms-server/server-config.example.json'])
        settings.update(max_players=5, experimental_first_slot=0)
        server_payload['wicked-private-realms-server/server-config.example.json'] = json.dumps(settings, indent=2).encode()
        server_payload['wicked-private-realms-server/FIVE-PLAYER-TEST.md'] = (ROOT / 'docs/FIVE-PLAYER-TEST.md').read_bytes()
    server = output / ('Wicked-Private-Realms-Server-' + VERSION + '.zip')
    with zipfile.ZipFile(server, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, raw in sorted(server_payload.items()):
            archive.writestr(name, raw)
    lines = []
    for path in (client, server):
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError('Invalid experimental archive')
        lines.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name)
        print('Prepared: ' + str(path))
    (output / 'SHA256SUMS.txt').write_text('\n'.join(lines) + '\n', encoding='ascii')


if __name__ == '__main__':
    main()
