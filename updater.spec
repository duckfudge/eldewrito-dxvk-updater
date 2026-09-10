# Build on Windows using the private repository's pinned dependencies.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
a = Analysis([str(root / 'launcher.py')], pathex=[str(root / 'src')],
             binaries=[], datas=collect_data_files('customtkinter'), hiddenimports=[],
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['pytest'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ElDewritoDXVKUpdater',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name='ElDewritoDXVKUpdater')
