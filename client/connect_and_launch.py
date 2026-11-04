"""Connect directly to a private TLS server and launch the build-checked client."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tls_transport import server_address
from client.steam_identity import bind_config,discover_identity,validate_steam_id
from client.install_gse import install_gse
from client.direct_connection import DirectBridge,remote_login


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true',help='Verify direct TLS login without starting the game')
    parser.add_argument('--login-stdin',action='store_true',help='Read server/steam_id/password/steam_mode as JSON from stdin')
    parser.add_argument('--cancel-file',type=Path)
    options=parser.parse_args()
    if not options.login_stdin:raise RuntimeError('Use the launcher to enter the server address, password and Steam ID')
    credentials=json.loads(sys.stdin.readline())
    address=server_address(credentials.get('server',''))
    identifier=validate_steam_id(credentials.get('steam_id',''))
    def cancelled():return options.cancel_file and options.cancel_file.exists()
    if cancelled():raise KeyboardInterrupt
    if not (ROOT.parent/'NoRestForTheWicked.exe').is_file():
        raise RuntimeError('Place this client folder directly inside the game folder, beside NoRestForTheWicked.exe')
    if os.name=='nt':
        running=subprocess.run(['tasklist','/FI','IMAGENAME eq NoRestForTheWicked.exe','/FO','CSV','/NH'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
        if b'norestforthewicked.exe' in running.stdout.lower():raise RuntimeError('Close the running game before binding its Steam ID')
    mode=credentials.get('steam_mode','gse')
    if mode not in ('gse','native'):raise ValueError('Unknown Steam API mode')
    if mode=='gse':
        installed=install_gse(ROOT.parent,ROOT,identifier)
        print('GSE compatibility: '+('installed with backup at '+str(installed['backup']) if installed['installed'] else 'approved build verified'),flush=True)
    bound=bind_config(ROOT.parent,identifier,ROOT/'data/steam-config-backups')
    identity=discover_identity(ROOT.parent)
    if identity['steam_id']!=identifier:
        raise RuntimeError('Game Steam ID '+identity['steam_id']+' differs from entered ID '+identifier+'. For genuine Steam use the signed-in account ID.')
    print('Steam identity: '+identifier,flush=True)
    print('Steam configuration: '+('bound to configs.user.ini' if bound else 'using signed-in Steam account'),flush=True)
    if cancelled():raise KeyboardInterrupt
    print('Connecting directly to the private TLS server...',flush=True)
    profile=remote_login(address,ROOT/'data/server-trust',identifier,credentials.get('password'))
    credentials=None
    profile['display_name']=identity['display_name']
    print(('Created account: ' if profile['created'] else 'Signed in: ')+profile['steam_id'],flush=True)
    bridge=None;launcher=None;session_profile=ROOT/'data/launcher-session.json'
    try:
        bridge=DirectBridge(address,ROOT/'data/server-trust',profile['token']).start()
        session_profile.parent.mkdir(exist_ok=True)
        session_profile.write_text(json.dumps(profile,ensure_ascii=False),encoding='utf-8')
        if options.check:
            request=urllib.request.Request('http://127.0.0.1:18770/game/auth/v3/token',
                json.dumps({'entitlementTicket':profile['token'],'entitlementPlatform':'private'}).encode(),{'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=15) as response:session=json.load(response)['data'][0]
            request=urllib.request.Request('http://127.0.0.1:18770/game/rest/save/'+session['accountId']+'/ListRealms',
                b'{}',{'Authorization':'Bearer '+session['token'],'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=15) as response:realms=json.load(response)
            print('Direct TLS login and game API verified; '+str(len(realms))+' shared realms visible. No game was launched.',flush=True)
            return
        if cancelled():raise KeyboardInterrupt
        runtime=ROOT/'runtime/python.exe';python=str(runtime) if runtime.is_file() else sys.executable
        args=[python,str(ROOT/'client/launch.py'),'--profile',str(session_profile),'--transport-probe','--skip-prologue-entry-test']
        if options.cancel_file:args.extend(['--cancel-file',str(options.cancel_file)])
        print('Connected. Launching the private realm client. Keep this launcher open.',flush=True)
        launcher=subprocess.Popen(args,cwd=str(ROOT.parent),creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)
        while launcher.poll() is None:
            if cancelled():raise KeyboardInterrupt
            time.sleep(.5)
        if launcher.returncode:
            raise RuntimeError('Game adapter exited with code '+str(launcher.returncode)+'; see research/client-adapter.jsonl')
    finally:
        if launcher is not None and launcher.poll() is None:
            if options.cancel_file:options.cancel_file.write_text('stop')
            elif os.name=='nt':launcher.send_signal(signal.CTRL_BREAK_EVENT)
            else:launcher.send_signal(signal.SIGINT)
            try:launcher.wait(timeout=10)
            except subprocess.TimeoutExpired:launcher.kill();launcher.wait()
        if bridge is not None:bridge.close()
        session_profile.unlink(missing_ok=True)


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('Private client stopped.')
    except Exception as error:print('ERROR: '+str(error),file=sys.stderr);sys.exit(1)
