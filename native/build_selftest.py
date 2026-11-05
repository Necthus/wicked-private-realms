import argparse
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parent
def main():
    compiler=Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    parser=argparse.ArgumentParser();parser.add_argument('--experimental-five',action='store_true');args=parser.parse_args()
    out=ROOT/('dist-five' if args.experimental_five else 'dist');obj=ROOT/'selftest';obj.mkdir(exist_ok=True)
    subprocess.run([str(compiler),'/nologo','/platform:x64','/target:exe','/optimize+','/out:'+str(obj/'WickedAdapterSelfTest.exe'),str(ROOT/'selftest_host.cs')],check=True)
    subprocess.run([str(obj/'WickedAdapterSelfTest.exe')]+(['--five-player'] if args.experimental_five else []),cwd=out,check=True)
if __name__=='__main__':main()
