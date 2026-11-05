"""Install the verified bundled GSE build, preserving a recoverable backup."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from client.steam_identity import validate_steam_id,bind_config,configured_identity
PINNED_GSE_SHA256='723e13f5a2e1ec799f342612a3294229ca832d143fdea85c00025216b6696e88'


def sha256(path):
    with Path(path).open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()


def install_gse(game,client_root,identifier):
    identifier=validate_steam_id(identifier);game=Path(game).resolve();client_root=Path(client_root).resolve()
    source=client_root/'plugin/gse';manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    if manifest['dll_sha256']!=PINNED_GSE_SHA256 or sha256(source/'steam_api64.dll')!=PINNED_GSE_SHA256:
        raise ValueError('Bundled GSE does not match the approved build')
    for relative,expected in manifest['files'].items():
        parts=PurePosixPath(relative)
        if parts.is_absolute() or '..' in parts.parts or '\\' in relative:raise ValueError('Invalid GSE manifest path')
        if sha256(source/relative)!=expected:raise ValueError('Bundled GSE file failed integrity check: '+relative)
    fingerprint=json.loads((client_root/'research/client-fingerprint.json').read_text())['GameAssembly.dll']['sha256']
    if sha256(game/'GameAssembly.dll')!=fingerprint:raise ValueError('Unsupported game build; GSE installation cancelled')
    target=game/'NoRestForTheWicked_Data/Plugins/x86_64';target.mkdir(parents=True,exist_ok=True)
    current=target/'steam_api64.dll'
    same=current.is_file() and sha256(current)==PINNED_GSE_SHA256
    settings=source/'steam_settings'
    writes=[]
    if not same:writes.append((source/'steam_api64.dll',current))
    for file in sorted(settings.rglob('*')):
        if file.is_file():
            destination=target/'steam_settings'/file.relative_to(settings)
            if not same or not destination.exists():writes.append((file,destination))
    backup=None
    if writes:
        backup=client_root/'data/gse-backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup.mkdir(parents=True)
        if current.exists():shutil.copy2(current,backup/'steam_api64.dll')
        if (target/'steam_settings').exists():shutil.copytree(target/'steam_settings',backup/'steam_settings')
        originals={str(destination):destination.read_bytes() if destination.exists() else None for _,destination in writes}
        completed=[]
        try:
            for file,destination in writes:
                destination.parent.mkdir(parents=True,exist_ok=True)
                temporary=destination.with_name(destination.name+'.wicked.tmp')
                try:shutil.copy2(file,temporary);os.replace(temporary,destination)
                finally:temporary.unlink(missing_ok=True)
                completed.append(destination)
        except BaseException:
            for destination in reversed(completed):
                original=originals[str(destination)]
                if original is None:destination.unlink(missing_ok=True)
                else:destination.write_bytes(original)
            raise
    bind_config(game,identifier,client_root/'data/steam-config-backups')
    return {'installed':not same,'backup':str(backup) if backup else None,'steam_id':identifier,'sha256':PINNED_GSE_SHA256}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--steam-id');parser.add_argument('--game',type=Path,default=ROOT.parent)
    args=parser.parse_args()
    try:
        identifier=args.steam_id
        if not identifier:
            try:identifier=configured_identity(args.game)
            except ValueError:identifier=None
        if not identifier:identifier=input('SteamID64 (17 digits): ').strip()
        print(json.dumps(install_gse(args.game,ROOT,identifier),indent=2))
    except Exception as error:raise SystemExit(str(error))
