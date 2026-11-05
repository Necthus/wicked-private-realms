"""Read the installed game's Steam API identity in a short-lived helper process."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import re
import shutil
from datetime import datetime,timezone

APP_ID='1371980'
ROOT=Path(__file__).resolve().parents[1]


def config_path(game):
    return Path(game)/'NoRestForTheWicked_Data/Plugins/x86_64/steam_settings/configs.user.ini'


def configured_identity(game):
    path=config_path(game)
    if not path.is_file():return None
    text=path.read_text(encoding='utf-8-sig')
    section=re.search(r'(?ms)^\[user::general\][^\r\n]*\r?\n(.*?)(?=^\[|\Z)',text)
    if not section:return None
    value=re.search(r'(?m)^\s*account_steamid\s*=\s*([0-9]+)\s*$',section.group(1))
    return validate_steam_id(value.group(1)) if value else None


def bind_config(game,identifier,backup_directory):
    identifier=validate_steam_id(identifier)
    path=config_path(game)
    if not path.is_file():return False  # Genuine installs use the actual Steam session.
    raw=path.read_bytes();text=raw.decode('utf-8-sig')
    section=re.search(r'(?ms)^\[user::general\][^\r\n]*\r?\n(.*?)(?=^\[|\Z)',text)
    if not section:raise RuntimeError('configs.user.ini does not contain [user::general]')
    body=section.group(1)
    entry=re.compile(r'(?m)^(\s*account_steamid\s*=\s*)[^\r\n]*')
    newline='\r\n' if '\r\n' in text else '\n'
    body=entry.sub(lambda match:match.group(1)+identifier,body) if entry.search(body) else body+'account_steamid='+identifier+newline
    updated=text[:section.start(1)]+body+text[section.end(1):]
    encoded=(b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b'')+updated.encode('utf-8')
    if encoded==raw:return True
    backup_directory=Path(backup_directory);backup_directory.mkdir(parents=True,exist_ok=True)
    backup=backup_directory/('configs.user-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.ini')
    shutil.copy2(path,backup)
    temporary=path.with_name(path.name+'.wicked.tmp')
    try:temporary.write_bytes(encoded);os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
    return True


def validate_steam_id(value):
    if not isinstance(value,str) or len(value)!=17 or not value.isascii() or not value.isdecimal():
        raise ValueError('Steam did not return a valid SteamID64')
    number=int(value)
    if number>>56!=1 or number>>52&15!=1 or number>>32&0xfffff!=1 or not number&0xffffffff:
        raise ValueError('Steam did not return a public individual Steam account')
    return value


def native_identity(game):
    if os.name!='nt':raise RuntimeError('Steam identity discovery requires Windows x64')
    game=Path(game).resolve()
    library=game/'NoRestForTheWicked_Data/Plugins/x86_64/steam_api64.dll'
    if not library.is_file():raise RuntimeError('The game Steam API DLL was not found')
    os.chdir(game)
    os.environ['SteamAppId']=APP_ID;os.environ['SteamGameId']=APP_ID
    with os.add_dll_directory(str(library.parent)):
        steam=ctypes.CDLL(str(library))
        initialize=steam.SteamAPI_InitSafe;initialize.argtypes=[];initialize.restype=ctypes.c_bool
        shutdown=steam.SteamAPI_Shutdown;shutdown.argtypes=[];shutdown.restype=None
        if not initialize():raise RuntimeError('Steam initialization failed. Start Steam and sign in to the game account first.')
        try:
            def interface(prefix,versions):
                for version in versions:
                    method=getattr(steam,prefix+str(version).zfill(3),None)
                    if method is not None:
                        method.argtypes=[];method.restype=ctypes.c_void_p
                        pointer=method()
                        if pointer:return pointer
                raise RuntimeError('Unsupported Steam interface version')
            user=interface('SteamAPI_SteamUser_v',range(23,19,-1))
            get_id=steam.SteamAPI_ISteamUser_GetSteamID
            get_id.argtypes=[ctypes.c_void_p];get_id.restype=ctypes.c_uint64
            identifier=validate_steam_id(str(get_id(user)))
            friends=interface('SteamAPI_SteamFriends_v',range(18,16,-1))
            persona=steam.SteamAPI_ISteamFriends_GetPersonaName
            persona.argtypes=[ctypes.c_void_p];persona.restype=ctypes.c_char_p
            return {'steam_id':identifier,'display_name':(persona(friends) or b'').decode('utf-8',errors='replace')}
        finally:shutdown()


def discover_identity(game=None):
    game=Path(game or ROOT.parent).resolve()
    env=dict(os.environ,PYTHONIOENCODING='utf-8')
    try:
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--native',str(game)],
            cwd=game,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    except subprocess.TimeoutExpired:raise RuntimeError('Steam identity discovery timed out') from None
    for line in result.stdout.splitlines():
        if line.startswith('WICKED_STEAM_IDENTITY='):
            value=json.loads(line.split('=',1)[1]);validate_steam_id(value['steam_id'])
            if result.returncode==0:return value
    raise RuntimeError('Cannot read game Steam identity: '+(result.stderr.strip()[-1000:] or 'native helper exited with '+str(result.returncode)))


if __name__=='__main__':
    try:
        value=native_identity(sys.argv[2]) if len(sys.argv)>2 and sys.argv[1]=='--native' else discover_identity()
        print('WICKED_STEAM_IDENTITY='+json.dumps(value,ensure_ascii=True),flush=True)
    except Exception as error:print(str(error),file=sys.stderr);sys.exit(1)
