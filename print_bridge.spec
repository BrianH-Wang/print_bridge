# -*- mode: python ; coding: utf-8 -*-
# 跨平台 PyInstaller spec
#   - Windows: pyinstaller --noconfirm print_bridge.spec
#       -> dist/print_bridge/print_bridge.exe   (onedir)
#   - macOS:   pyinstaller --noconfirm print_bridge.spec
#       -> dist/Print Bridge.app                 (.app bundle)
#       可继续: bash scripts/make_dmg.sh         -> dist/Print Bridge.dmg
#   - Linux:   pyinstaller --noconfirm print_bridge.spec
#       -> dist/print_bridge/print_bridge        (onedir)
#
# macOS 上 v7 已经禁止 onefile + windowed;Windows / Linux 用 onedir 也更稳。
import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter', '_tkinter',
        'test', 'unittest', 'xml.sax.expatreader',
        'numpy', 'pandas', 'PIL', 'matplotlib',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onedir:输出 dist/print_bridge/print_bridge(.exe),Windows/Linux 直接进这个目录
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,         # onedir 关键:把 bin/datas 放到 COLLAPSE 到 COLLECT
    name='print_bridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='print_bridge',
)

# macOS 把整个 dist/print_bridge 包成 .app
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='Print Bridge.app',
        icon=None,
        bundle_identifier='com.printbridge.app',
        info_plist={
            'CFBundleName': 'Print Bridge',
            'CFBundleDisplayName': 'Print Bridge',
            'CFBundleVersion': '1.0.0',
            'CFBundleShortVersionString': '1.0.0',
            'LSUIElement': False,
            'NSHighResolutionCapable': True,
        },
    )
