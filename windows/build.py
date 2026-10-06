import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_release import VERSION


def main():
    if sys.platform != 'win32':
        raise SystemExit('O instalador Windows deve ser compilado em Windows.')
    from PIL import Image
    windows = ROOT / 'windows'
    staging = ROOT / 'installation/windows'
    staging.mkdir(parents=True, exist_ok=True)
    Image.open(ROOT / 'assets/agentes-locais.png').save(windows / 'MUR.ico', sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    (windows / 'version-info.txt').write_text(f"""VSVersionInfo(ffi=FixedFileInfo(filevers=(3,0,6,0),prodvers=(3,0,6,0),mask=0x3f,flags=0x2,OS=0x40004,fileType=0x1,subtype=0x0,date=(0,0)),kids=[StringFileInfo([StringTable('040904B0',[StringStruct('CompanyName','MUR'),StringStruct('FileDescription','Model Usage Reports'),StringStruct('FileVersion','{VERSION}'),StringStruct('InternalName','MUR'),StringStruct('OriginalFilename','MUR.exe'),StringStruct('ProductName','MUR'),StringStruct('ProductVersion','{VERSION}')])]),VarFileInfo([VarStruct('Translation',[1033,1200])])])""", encoding='utf-8')
    bootstrap = staging / 'MicrosoftEdgeWebview2Setup.exe'
    if not bootstrap.exists():
        urllib.request.urlretrieve('https://go.microsoft.com/fwlink/p/?LinkId=2124703', bootstrap)
    powershell = shutil.which('pwsh') or shutil.which('powershell')
    subprocess.run([powershell, '-NoProfile', '-Command',
                    "$s=Get-AuthenticodeSignature -LiteralPath $env:MUR_WEBVIEW_BOOTSTRAP; if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notlike '*Microsoft Corporation*') {exit 1}"],
                   env=dict(os.environ, MUR_WEBVIEW_BOOTSTRAP=str(bootstrap)), check=True)
    releases = ROOT / 'dist/windows'
    releases.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(releases / 'app'),
                    '--workpath', str(staging / 'pyinstaller'), str(windows / 'MUR.spec')], check=True, cwd=ROOT)
    bundle = releases / 'app/MUR'
    forbidden = {'settings.json', 'auth.json', 'account-observations.json', 'receipts.json', 'raw.json', 'report.html'}
    for path in bundle.rglob('*'):
        if path.is_file() and (path.name in forbidden or path.suffix in ('.sqlite3', '.jsonl', '.mur', '.p12', '.key')):
            raise RuntimeError('Arquivo privado incluído no bundle: ' + str(path.relative_to(bundle)))
    compiler = Path(r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe')
    subprocess.run([str(compiler), '/DReleaseVersion=' + VERSION, str(windows / 'installer.iss')], check=True, cwd=windows)
    archive = Path(shutil.make_archive(str(releases / ('MUR-' + VERSION + '-windows-x64-portable')), 'zip', bundle.parent, bundle.name))
    installer = releases / ('MUR-' + VERSION + '-windows-x64-setup.exe')
    (releases / 'SHA256SUMS-windows.txt').write_text(''.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n' for path in (installer, archive)), encoding='utf-8')
    (releases / 'build-windows.json').write_text(json.dumps({'version': VERSION, 'platform': 'windows', 'architecture': 'x64',
        'privateDataIncluded': False, 'authenticodeSigned': False, 'webviewBootstrapMicrosoftSignatureVerified': True}, indent=2), encoding='utf-8')
    print(installer)


if __name__ == '__main__':
    main()
