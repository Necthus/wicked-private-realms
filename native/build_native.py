"""Build the native DLL with installed MSVC tools; no build runtime is shipped."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys

ROOT=Path(__file__).resolve().parent


def vc_environment():
    vswhere=Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Microsoft Visual Studio/Installer/vswhere.exe'
    install=subprocess.check_output([str(vswhere),'-latest','-products','*','-property','installationPath'],text=True).strip()
    env={key.upper():value for key,value in os.environ.items()}
    version_key=lambda path:tuple(int(part) for part in path.name.split('.'))
    vc=max((p for p in (Path(install)/'VC/Tools/MSVC').iterdir() if p.is_dir() and p.name.replace('.','').isdigit()),key=version_key)
    kits=Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Windows Kits/10'
    sdk=max((p for p in (kits/'Include').iterdir() if p.is_dir() and p.name.replace('.','').isdigit()),key=version_key)
    env['PATH']=str(vc/'bin/Hostx64/x64')+os.pathsep+str(kits/'bin'/sdk.name/'x64')+os.pathsep+env.get('PATH','')
    env['INCLUDE']=';'.join(map(str,[vc/'include',sdk/'ucrt',sdk/'shared',sdk/'um',sdk/'winrt']))
    env['LIB']=';'.join(map(str,[vc/'lib/x64',kits/'Lib'/sdk.name/'ucrt/x64',kits/'Lib'/sdk.name/'um/x64']))
    return env


def build():
    output=ROOT/'dist';output.mkdir(exist_ok=True)
    obj=ROOT/'build';obj.mkdir(exist_ok=True)
    sources=[ROOT/'adapter/adapter.c']+[ROOT/'vendor/minhook/src'/name for name in ('buffer.c','hook.c','trampoline.c','hde/hde64.c')]
    command=['cl.exe','/nologo','/O2','/MT','/W3','/wd4152','/std:c11','/DUNICODE','/D_UNICODE','/LD',
        '/I'+str(ROOT/'vendor/minhook/include'),'/Fo'+str(obj)+os.sep,*map(str,sources),
        '/link','/INCREMENTAL:NO','/OUT:'+str(output/'WickedAdapter.dll'),'/IMPLIB:'+str(obj/'WickedAdapter.lib'),'ws2_32.lib']
    env=vc_environment();command[0]=shutil.which('cl.exe',path=env['PATH']) or 'cl.exe'
    subprocess.run(command,cwd=obj,env=env,check=True)
    print('Built '+str(output/'WickedAdapter.dll'))


if __name__=='__main__':build()
