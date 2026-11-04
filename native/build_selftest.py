import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parent
def main():
    compiler=Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    out=ROOT/'dist';obj=ROOT/'selftest';obj.mkdir(exist_ok=True)
    subprocess.run([str(compiler),'/nologo','/target:exe','/optimize+','/out:'+str(obj/'WickedAdapterSelfTest.exe'),str(ROOT/'selftest_host.cs')],check=True)
    subprocess.run([str(obj/'WickedAdapterSelfTest.exe')],cwd=out,check=True)
if __name__=='__main__':main()
