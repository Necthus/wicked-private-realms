"""Build the no-Python Windows client package."""
from pathlib import Path
import hashlib,json,shutil,zipfile
ROOT=Path(__file__).resolve().parent.parent
VERSION='v0.2.1-native';NATIVE=ROOT/'native';OUT=ROOT/('releases/public-'+VERSION);OUT.mkdir(parents=True,exist_ok=True)

def add(archive,name,path):archive.write(path,'wicked-client/'+name)

def main():
    # The binaries are built by build_native.py/build_host.py before this script.
    required=[NATIVE/'dist/WickedAdapter.dll',NATIVE/'dist/WickedNativeHost.exe']
    for path in required:
        if not path.is_file():raise SystemExit('Build missing: '+str(path))
    with zipfile.ZipFile(OUT/('Wicked-Private-Realms-Client-Native-v29466-'+VERSION+'.zip'),'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name in ('WickedLauncher.exe','WickedLauncher.exe.config','Start-Launcher.cmd','Start-Test.cmd','Check-Connection.cmd'):
            add(archive,name,ROOT/name)
        add(archive,'WickedNativeHost.exe',NATIVE/'dist/WickedNativeHost.exe');add(archive,'WickedAdapter.dll',NATIVE/'dist/WickedAdapter.dll')
        for path in (ROOT/'plugin/gse').rglob('*'):
            if path.is_file():add(archive,path.relative_to(ROOT).as_posix(),path)
        for name in ('CLIENT.md','RELEASE_NOTES.md'):
            add(archive,'docs/'+name,ROOT/'docs'/name)
        add(archive,'LICENSE',ROOT/'LICENSE');add(archive,'THIRD_PARTY.md',ROOT/'THIRD_PARTY.md')
        archive.writestr('wicked-client/data/launcher-settings.json',json.dumps({'server':'','steam_id':'','steam_mode':'gse'},indent=2))
        archive.writestr('wicked-client/native/README.md',(NATIVE/'README.md').read_bytes())
        archive.writestr('wicked-client/native/BUILD-METADATA.json',json.dumps({'mode':'native','python_runtime_included':False,'frida_runtime_included':False,'game_build':'29466','adapter':'WickedAdapter.dll','host':'WickedNativeHost.exe'},indent=2))
    path=OUT/('Wicked-Private-Realms-Client-Native-v29466-'+VERSION+'.zip')
    with path.open('rb') as file:digest=hashlib.file_digest(file,'sha256').hexdigest()
    (OUT/'SHA256SUMS.txt').write_text(digest+'  '+path.name+'\n',encoding='ascii')
    print(json.dumps({'asset':path.name,'bytes':path.stat().st_size,'sha256':digest}))

if __name__=='__main__':main()
