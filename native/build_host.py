from pathlib import Path
import os,subprocess
ROOT=Path(__file__).resolve().parent
if __name__=='__main__':
    compiler=Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    out=ROOT/'dist';out.mkdir(exist_ok=True)
    subprocess.run([str(compiler),'/nologo','/target:exe','/optimize+','/reference:System.Web.Extensions.dll','/out:'+str(out/'WickedNativeHost.exe'),str(ROOT/'WickedNativeHost.cs')],check=True)
