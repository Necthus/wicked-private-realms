"""Deploy a native Release ZIP beside the game; preserve local client settings."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-directory', type=Path)
    parser.add_argument('--release', type=Path)
    parser.add_argument('--check', action='store_true', help='Verify without writing files')
    args = parser.parse_args()
    local_path = ROOT / 'workspace.local.json'
    local = json.loads(local_path.read_text(encoding='utf-8')) if local_path.exists() else {}
    game = args.game_directory or Path(local.get('game_directory', ''))
    package = args.release or Path(local.get('native_release', ''))
    if not package.is_absolute():
        package = ROOT / package
    game = game.resolve()
    if not (game / 'NoRestForTheWicked.exe').is_file():
        raise ValueError('Specify the game directory containing NoRestForTheWicked.exe')
    destination = game / 'wicked-client'
    if destination.is_symlink() or destination.is_junction():
        raise ValueError('Client directory must not be a link')
    files = {}
    with zipfile.ZipFile(package) as archive:
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if (not path.parts or path.parts[0] != 'wicked-client'
                    or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename):
                raise ValueError('Unexpected ZIP path: ' + entry.filename)
            if entry.is_dir():
                continue
            relative = Path(*path.parts[1:])
            if relative.suffix.lower() in ('.py', '.js', '.pyc', '.key', '.pem', '.sqlite3'):
                raise ValueError('Unexpected source or private file: ' + entry.filename)
            if relative in files:
                raise ValueError('Duplicate ZIP entry: ' + entry.filename)
            files[relative] = archive.read(entry)
    metadata = json.loads(files[Path('native/BUILD-METADATA.json')])
    if metadata.get('mode') != 'native':
        raise ValueError('This tool requires a native client release')
    for name, expected in metadata['files'].items():
        if hashlib.sha256(files[Path(name)]).hexdigest() != expected:
            raise ValueError('Release binary hash mismatch: ' + name)
    for relative, content in files.items():
        target = destination / relative
        if not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError('Client file escapes deployment directory')
        if relative == Path('data/launcher-settings.json') and target.exists():
            continue
        if args.check:
            if not target.is_file() or target.read_bytes() != content:
                raise ValueError('Installed file differs from release: ' + str(relative))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    # Runtime-generated settings, trust pins, backups and logs are retained.
    allowed_runtime = {'data', 'research', 'logs'}
    extras = [str(p.relative_to(destination)) for p in destination.rglob('*')
              if p.is_file() and p.relative_to(destination) not in files
              and p.relative_to(destination).parts[0] not in allowed_runtime]
    if extras:
        raise ValueError('Unexpected files in deployment: ' + ', '.join(extras))
    print(('Verified' if args.check else 'Deployed') + ': ' + str(destination))
    print('Release files: ' + str(len(files)))


if __name__ == '__main__':
    main()
