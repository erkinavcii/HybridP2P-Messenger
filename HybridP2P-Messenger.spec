# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# Bundle Flet 0.85 icon JSON resources (material/cupertino icons) and desktop/local submodules
datas = collect_data_files('flet')
binaries = []
# Marka dosyaları (pencere ikonu, giriş ekranı görselleri) — client.ASSETS_DIR bunları arar
datas += [('assets/brand/app.ico', 'assets/brand'),
          ('assets/brand/icon.png', 'assets/brand'),
          ('assets/brand/splash.png', 'assets/brand')]
hiddenimports = collect_submodules('desktop')

fd_datas, fd_binaries, fd_hidden = collect_all('flet_desktop')
datas += fd_datas
binaries += fd_binaries
hiddenimports += fd_hidden

a = Analysis(
    ['client.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', '_pytest', 'flet.testing', 'flet.cli', 'flet.fastapi'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='HybridP2P-Messenger',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/brand/app.ico',
)
