"""Build the standalone Python server ZIP from the maintained server/ sources."""
import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'v0.1.4'


def build(output):
    entries = {p.name: p.read_bytes() for p in (ROOT / 'server').glob('*.py')}
    for name, source in {
        'requirements.txt': 'server/requirements.txt',
        'server-config.example.json': 'deploy/server-config.example.json',
        'wicked-private-realms.service': 'deploy/standalone.service',
        'README.md': 'docs/SERVER.md', 'LICENSE': 'LICENSE', 'THIRD_PARTY.md': 'THIRD_PARTY.md',
    }.items():
        entries[name] = (ROOT / source).read_bytes()
    entries['Dockerfile'] = (ROOT / 'deploy/Dockerfile').read_text().replace('COPY server/requirements.txt ./', 'COPY requirements.txt ./').replace('COPY server/ ./', 'COPY . ./').encode()
    entries['compose.yaml'] = (ROOT / 'deploy/compose.yaml').read_text().replace('    build:\n      context: ..\n      dockerfile: deploy/Dockerfile', '    build: .').encode()
    entries['.dockerignore'] = b'server-data/\nserver-config.json\n*.pem\n*.key\n.venv/\n__pycache__/\n*.sqlite3*\n'
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / ('Wicked-Private-Realms-Server-' + VERSION + '.zip')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, raw in sorted(entries.items()):
            archive.writestr('wicked-private-realms-server/' + name, raw)
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError('Invalid ZIP')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (output / 'SHA256SUMS.txt').write_text(digest + '  ' + path.name + '\n', encoding='ascii')
    print('Built ' + str(path))
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'releases/server-build')
    args = parser.parse_args()
    build(args.output)
