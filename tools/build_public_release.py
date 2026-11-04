"""Build separate server/client releases from a clean public source checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION='v0.1.0'
SERVER_MODULES=['server.py','game_backend.py','launcher_auth.py','photon_wire.py','quantum_wire.py',
    'room_simulation.py','save_wire.py','shared_saves.py','public_gateway.py','tls_transport.py','run_private_server.py']
CLIENT_FILES=['WickedLauncher.exe','WickedLauncher.exe.config','Start-Launcher.cmd','Start-Test.cmd',
    'Check-Connection.cmd','Install-GSE.cmd','tls_transport.py','research/client-fingerprint.json']
CLIENT_FILES+=['client/'+name for name in ('launch.py','redirect.js','ping_fix.js','client_identity.py',
    'steam_identity.py','install_gse.py','direct_connection.py','connect_and_launch.py','launcher.cs',
    'launcher.manifest','LAUNCHER-README.md')]


def create_zip(path,folder,entries):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,source in sorted(entries.items()):
            name=folder+'/'+name
            if isinstance(source,bytes):archive.writestr(name,source)
            else:archive.write(source,name)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        for name in archive.namelist():
            if any(part in name.split('/') for part in ('transport','server-data','server-trust','gse-backups','__pycache__')):
                raise ValueError('Private/runtime state in release: '+name)
            if name.endswith(('.sqlite3','.sqlite3-wal','.sqlite3-shm','client_key','server-key.pem','server-secret.json')):
                raise ValueError('Sensitive file in release: '+name)
    with path.open('rb') as file:digest=hashlib.file_digest(file,'sha256').hexdigest()
    print(json.dumps({'asset':path.name,'bytes':path.stat().st_size,'sha256':digest}),flush=True)
    return digest+'  '+path.name


def build(root,output,runtime=None,frida=None):
    root=Path(root);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    server={name:root/name for name in SERVER_MODULES}
    server.update({'tools/photon_tcp_probe.py':root/'tools/photon_tcp_probe.py','requirements.txt':root/'requirements.txt',
        'server-config.example.json':root/'deploy/server-config.example.json','Dockerfile':root/'deploy/Dockerfile',
        'compose.yaml':root/'deploy/compose.yaml','wicked-private-realms.service':root/'deploy/standalone.service',
        'README.md':root/'docs/SERVER.md','LICENSE':root/'LICENSE','THIRD_PARTY.md':root/'THIRD_PARTY.md'})
    checks=[create_zip(output/('Wicked-Private-Realms-Server-'+VERSION+'.zip'),'wicked-private-realms-server',server)]
    client={name:root/name for name in CLIENT_FILES}
    client.update({'README.md':root/'docs/CLIENT.md','LICENSE':root/'LICENSE','THIRD_PARTY.md':root/'THIRD_PARTY.md',
        'data/launcher-settings.json':json.dumps({'server':'','steam_id':'','steam_mode':'gse'},indent=2).encode()})
    for path in (root/'plugin/gse').rglob('*'):
        if path.is_file():client[path.relative_to(root).as_posix()]=path
    checks.append(create_zip(output/('Wicked-Private-Realms-Client-Update-v29466-'+VERSION+'.zip'),'wicked-client',client))
    if runtime is not None and frida is not None:
        full=dict(client)
        for directory,prefix in [(Path(runtime),'runtime'),(Path(frida),'tools/frida-compat')]:
            for path in directory.rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':
                    full[prefix+'/'+path.relative_to(directory).as_posix()]=path
        if 'runtime/python.exe' not in full or 'tools/frida-compat/frida/__init__.py' not in full:
            raise ValueError('Full Windows runtime or Frida package is missing')
        checks.append(create_zip(output/('Wicked-Private-Realms-Client-v29466-'+VERSION+'.zip'),'wicked-client',full))
    (output/'SHA256SUMS.txt').write_text('\n'.join(checks)+'\n',encoding='ascii')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,default=ROOT/'releases/public-v0.1.0')
    parser.add_argument('--runtime',type=Path);parser.add_argument('--frida',type=Path)
    args=parser.parse_args();build(args.root,args.output,args.runtime,args.frida)
