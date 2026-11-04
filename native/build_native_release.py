"""Package only compiled client binaries and verified third-party GSE files."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT=Path(__file__).resolve().parent.parent
VERSION='v0.2.2-native'


def main():
    native=ROOT/'native';output=ROOT/'releases'/('public-'+VERSION);output.mkdir(parents=True,exist_ok=True)
    entries={name:ROOT/name for name in ('WickedLauncher.exe','WickedLauncher.exe.config','Start-Launcher.cmd','Start-Test.cmd','Check-Connection.cmd','LICENSE','THIRD_PARTY.md')}
    entries.update({name:native/'dist'/name for name in ('WickedNativeHost.exe','WickedAdapter.dll')})
    entries.update({p.relative_to(ROOT).as_posix():p for p in (ROOT/'plugin/gse').rglob('*') if p.is_file()})
    entries['README.md']=ROOT/'docs/NATIVE-MIGRATION.md'
    entries['native/MinHook-LICENSE.txt']=native/'vendor/minhook/LICENSE.txt'
    path=output/('Wicked-Private-Realms-Client-Native-v29466-'+VERSION+'.zip')
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,file in sorted(entries.items()):archive.write(file,'wicked-client/'+name)
        archive.writestr('wicked-client/data/launcher-settings.json',json.dumps({'server':'','steam_id':'','steam_mode':'gse'},indent=2))
        archive.writestr('wicked-client/native/BUILD-METADATA.json',json.dumps({'mode':'native','version':VERSION,
            'python_runtime_included':False,'frida_runtime_included':False,'game_build':'29466',
            'files':{name:hashlib.sha256(entries[name].read_bytes()).hexdigest() for name in ('WickedLauncher.exe','WickedNativeHost.exe','WickedAdapter.dll')}},indent=2))
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:raise ValueError('Invalid ZIP')
        for name in archive.namelist():
            if any(part in name.split('/') for part in ('runtime','transport','server-trust','server-data','integration','frida-compat')) or name.endswith(('.py','.js','.sqlite3','.key','.pem')):
                raise ValueError('Unexpected runtime or private state: '+name)
        if hashlib.sha256(archive.read('wicked-client/plugin/gse/steam_api64.dll')).hexdigest()!='723e13f5a2e1ec799f342612a3294229ca832d143fdea85c00025216b6696e88':raise ValueError('Wrong GSE DLL')
    with path.open('rb') as file:digest=hashlib.file_digest(file,'sha256').hexdigest()
    (output/'SHA256SUMS.txt').write_text(digest+'  '+path.name+'\n',encoding='ascii')
    print(json.dumps({'file':str(path),'bytes':path.stat().st_size,'sha256':digest}))


if __name__=='__main__':main()
