"""Package the native client from compiled output or a previously tested Release."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'v0.2.3'
BINARIES = ('WickedLauncher.exe', 'WickedNativeHost.exe', 'WickedAdapter.dll')
GSE_HASH = '723e13f5a2e1ec799f342612a3294229ca832d143fdea85c00025216b6696e88'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binaries-from', type=Path, help='Reuse verified EXE/DLL files from a tested native Release ZIP')
    parser.add_argument('--gse-from', type=Path, help='Native Release ZIP containing the approved GSE DLL (or an approved DLL file)')
    parser.add_argument('--output', type=Path, default=ROOT / 'releases' / ('public-' + VERSION))
    args = parser.parse_args()
    entries = {name: ROOT / name for name in ('LICENSE', 'THIRD_PARTY.md')}
    entries.update({name: ROOT / 'native/launcher' / name for name in
                    ('WickedLauncher.exe.config', 'Start-Launcher.cmd', 'Start-Test.cmd', 'Check-Connection.cmd')})
    entries.update({p.relative_to(ROOT).as_posix(): p for p in (ROOT / 'plugin/gse').rglob('*')
                    if p.is_file() and p.name != 'steam_api64.dll'})
    entries['README.md'] = ROOT / 'docs/NATIVE-MIGRATION.md'
    entries['native/MinHook-LICENSE.txt'] = ROOT / 'native/vendor/minhook/LICENSE.txt'
    binaries = {}
    if args.binaries_from:
        with zipfile.ZipFile(args.binaries_from) as tested:
            metadata = json.loads(tested.read('wicked-client/native/BUILD-METADATA.json'))
            if metadata.get('mode') != 'native' or metadata.get('game_build') != '29466':
                raise ValueError('Unsupported binary source')
            for name in BINARIES:
                raw = tested.read('wicked-client/' + name)
                if hashlib.sha256(raw).hexdigest() != metadata['files'][name]:
                    raise ValueError('Tested binary hash mismatch: ' + name)
                binaries[name] = raw
    else:
        binaries = {name: (ROOT / 'native/dist' / name).read_bytes() for name in BINARIES}
    gse_source = args.gse_from or args.binaries_from or ROOT / 'plugin/gse/steam_api64.dll'
    if not gse_source.is_file():
        raise ValueError('GSE binaries are Release assets. Supply --gse-from <native Release ZIP or approved DLL>')
    if zipfile.is_zipfile(gse_source):
        with zipfile.ZipFile(gse_source) as bundle:
            gse = bundle.read('wicked-client/plugin/gse/steam_api64.dll')
    else:
        gse = gse_source.read_bytes()
    if hashlib.sha256(gse).hexdigest() != GSE_HASH:
        raise ValueError('Wrong GSE DLL')
    payload = {name: file.read_bytes() for name, file in entries.items()}
    payload.update(binaries)
    payload['plugin/gse/steam_api64.dll'] = gse
    payload['data/launcher-settings.json'] = json.dumps({'server': '', 'steam_id': '', 'steam_mode': 'gse'}, indent=2).encode()
    payload['native/BUILD-METADATA.json'] = json.dumps({
        'mode': 'native', 'version': VERSION, 'python_runtime_included': False,
        'frida_runtime_included': False, 'game_build': '29466',
        'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in binaries.items()},
    }, indent=2).encode()
    for name in payload:
        if (any(part in name.split('/') for part in ('runtime', 'transport', 'server-trust', 'server-data', 'integration', 'frida-compat'))
                or name.endswith(('.py', '.js', '.sqlite3', '.key', '.pem'))):
            raise ValueError('Unexpected source or private state: ' + name)
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / ('Wicked-Private-Realms-Client-Native-v29466-' + VERSION + '.zip')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, raw in sorted(payload.items()):
            archive.writestr('wicked-client/' + name, raw)
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError('Invalid ZIP')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (args.output / 'SHA256SUMS.txt').write_text(digest + '  ' + path.name + '\n', encoding='ascii')
    print(json.dumps({'file': str(path), 'bytes': path.stat().st_size, 'sha256': digest}))


if __name__ == '__main__':
    main()
