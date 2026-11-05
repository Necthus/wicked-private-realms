"""Build the WinForms launcher independently of the game installation."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    compiler = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([
        str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
        '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
        '/reference:System.Web.Extensions.dll', '/reference:System.Core.dll',
        '/win32manifest:' + str(ROOT / 'client/launcher.manifest'),
        '/out:' + str(ROOT / 'WickedLauncher.exe'), str(ROOT / 'client/launcher.cs'),
    ], cwd=ROOT, check=True)
    print('Built ' + str(ROOT / 'WickedLauncher.exe'))


if __name__ == '__main__':
    main()
