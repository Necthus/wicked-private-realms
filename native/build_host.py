from pathlib import Path
import os,subprocess
ROOT=Path(__file__).resolve().parent
if __name__=='__main__':
    compiler=Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    out=ROOT/'dist';out.mkdir(exist_ok=True)
    sources=sorted((ROOT/'host').glob('*.cs'))+sorted((ROOT/'shared').glob('*.cs'))
    subprocess.run([str(compiler),'/nologo','/platform:x64','/target:exe','/optimize+','/reference:System.Web.Extensions.dll','/reference:System.Core.dll','/out:'+str(out/'WickedNativeHost.exe'),*map(str,sources)],check=True)
