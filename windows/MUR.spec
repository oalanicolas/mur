from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

root = Path(SPECPATH).parent
datas = [(str(root / 'pricing.json'), '.'), (str(root / 'DISTRIBUTION.md'), '.'),
         (str(root / 'windows/MUR.ico'), '.')]
for file in (root / 'web').rglob('*'):
    if file.is_file() and file.suffix in ('.html', '.js', '.css', '.svg', '.woff2', '.txt'):
        datas.append((str(file), str(file.parent.relative_to(root))))
view_data, view_binaries, view_imports = collect_all('webview')
datas += view_data + collect_data_files('tzdata') + collect_data_files('tzlocal')
a = Analysis([str(root / 'windows/main.py')], pathex=[str(root)], binaries=view_binaries,
             datas=datas, hiddenimports=view_imports + ['webview.platforms.winforms'],
             excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi', 'cefpython3'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [('X utf8', None, 'OPTION')], exclude_binaries=True, name='MUR',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          icon=str(root / 'windows/MUR.ico'), version=str(root / 'windows/version-info.txt'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MUR')
