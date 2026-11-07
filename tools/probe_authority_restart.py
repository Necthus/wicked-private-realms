"""Verify a newly launched authority can resume and re-save a captured checkpoint."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'server'))

from authoritative_checkpoints import CheckpointStore
from tools.authority_probe.restart import verify_restart
from tools.probe_authority_runtime import verify_installation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe-directory', type=Path, required=True)
    parser.add_argument('--game-directory', type=Path)
    parser.add_argument('--seconds', type=int, default=120)
    args = parser.parse_args(argv)
    try:
        if not 30 <= args.seconds <= 600:
            raise ValueError('--seconds must be between 30 and 600')
        game = args.game_directory or Path(json.loads((ROOT / 'workspace.local.json').read_text(encoding='utf-8-sig'))['game_directory'])
        fingerprints = verify_installation(game)
        build = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
        path = args.probe_directory.resolve() / 'checkpoints.sqlite3'
        if not path.is_file():
            raise ValueError('The probe directory has no durable authority checkpoint database')
        with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)) as db:
            realms = db.execute('SELECT realm FROM authority_checkpoints').fetchall()
        if len(realms) != 1:
            raise ValueError('Restart probe requires exactly one checkpoint realm')
        store = CheckpointStore(path)
        realm = realms[0][0]
        checkpoint = store.load(realm, build)
        report = verify_restart(game.resolve(), fingerprints, checkpoint, args.probe_directory.resolve(), ROOT,
                                args.seconds, store, realm, build)
        print(json.dumps({'result': report}), flush=True)
        return 0 if report['probe_passed'] else 1
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
