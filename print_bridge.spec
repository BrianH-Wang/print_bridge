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
import os
import shutil
import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[(os.path.join(SPECPATH, 'assets', 'logo.png'), 'assets')],  # 窗口 / 任务栏图标
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
    # Windows 的 exe 图标；macOS 的图标在下面 BUNDLE 里给 .icns
    icon=os.path.join(SPECPATH, 'assets', 'icon.ico') if sys.platform == 'win32' else None,
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

# 现成的配置文件（configs/*.json）放在可执行文件旁边，让人能直接看到、直接导入。
# 不走 datas：那会被埋进 _internal/ 或 .app 里，使用者找不到。
# macOS 的 DMG 由 scripts/make_dmg.sh 把同一个目录拷到 .app 旁边。
_configs_src = os.path.join(SPECPATH, 'configs')
_configs_dst = os.path.join(DISTPATH, 'print_bridge', 'configs')
if os.path.isdir(_configs_src):
    shutil.rmtree(_configs_dst, ignore_errors=True)
    shutil.copytree(_configs_src, _configs_dst,
                    ignore=shutil.ignore_patterns('.DS_Store', '__pycache__'))

# 使用者手册放在可执行文件旁边：使用说明 / 高级配置说明（中文）、User Guide / Advanced Guide（English）。
# 仓库里维护的是 docs/ 下的 Markdown，这里现转成 HTML —— 使用者的电脑上没有看 .md 的东西，
# .html 双击就能开。只带这两份：开发指南、高级配置是给开发者看的，看仓库里的就够了。
import subprocess
_manuals_dir = os.path.join(SPECPATH, 'build', 'manuals')
subprocess.run([sys.executable, os.path.join(SPECPATH, 'scripts', 'build_manuals.py'), _manuals_dir],
               check=True)
_pkg_root = os.path.join(DISTPATH, 'print_bridge')
for _manual in ('使用说明.html', 'User Guide.html', '高级配置说明.html', 'Advanced Guide.html'):
    shutil.copy2(os.path.join(_manuals_dir, _manual), os.path.join(_pkg_root, _manual))

# macOS 把整个 dist/print_bridge 包成 .app
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='Print Bridge.app',
        icon=os.path.join(SPECPATH, 'assets', 'icon.icns'),
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
